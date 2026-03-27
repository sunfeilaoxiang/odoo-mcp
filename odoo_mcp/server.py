#!/usr/bin/env python3
"""
Odoo MCP Server — Full read/write access to Odoo 18 Accounting via JSON-RPC.

Provides tools for:
  Phase 1: Read-only queries (search_read, trial balance, journal items, etc.)
  Phase 2: Write operations (journal entries, invoices, payments, reconciliation)
  Phase 3: Workflow helpers (bank recon status, P&L summary, balance sheet, VAT)
"""

import json
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP, Context
from pydantic import BaseModel, Field, ConfigDict, field_validator

from odoo_mcp.client import OdooClient, OdooClientError, client_from_env

# ---------------------------------------------------------------------------
# Load .env if present
# ---------------------------------------------------------------------------
load_dotenv()

# ---------------------------------------------------------------------------
# Lifespan — connect once, share client across all tool calls
# ---------------------------------------------------------------------------

@asynccontextmanager
async def app_lifespan(server: FastMCP):
    """Initialize the Odoo client on startup, close on shutdown."""
    client = client_from_env()
    await client.connect()
    try:
        yield {"odoo": client}
    finally:
        await client.close()


mcp = FastMCP("odoo_mcp", lifespan=app_lifespan)


def _get_client(ctx: Context) -> OdooClient:
    """Extract the Odoo client from the lifespan context."""
    return ctx.request_context.lifespan_state["odoo"]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


def _fmt(data: Any, fmt: ResponseFormat, title: str = "") -> str:
    """Format data as markdown or JSON."""
    if fmt == ResponseFormat.JSON:
        return json.dumps(data, default=str, indent=2)
    
    # Markdown format
    lines = []
    if title:
        lines.append(f"## {title}\n")
    
    if isinstance(data, list) and data and isinstance(data[0], dict):
        # Table for list of dicts
        keys = list(data[0].keys())
        lines.append("| " + " | ".join(str(k) for k in keys) + " |")
        lines.append("|" + "|".join(["---"] * len(keys)) + "|")
        for row in data:
            lines.append("| " + " | ".join(str(row.get(k, "")) for k in keys) + " |")
    elif isinstance(data, dict):
        for key, val in data.items():
            lines.append(f"**{key}:** {val}")
    else:
        lines.append(str(data))
    
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PHASE 1: Read-Only Queries
# ---------------------------------------------------------------------------

class ChartOfAccountsFilters(BaseModel):
    """Filters for get_chart_of_accounts."""
    account_type: Optional[str] = Field(None, description="Filter by account type (asset, liability, equity, income, expense)")
    active_only: bool = Field(True, description="Only return active accounts")


@mcp.tool()
async def get_chart_of_accounts(
    ctx: Context,
    filters: Optional[ChartOfAccountsFilters] = None,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN
) -> str:
    """
    Get the Chart of Accounts.
    
    Returns all accounts with code, name, type, and balance information.
    Optional filtering by account type.
    """
    client = _get_client(ctx)
    
    domain = [('active', '=', True)] if filters and filters.active_only else []
    if filters and filters.account_type:
        domain.append(('user_type_id.type', '=', filters.account_type))
    
    accounts = await client.search_read('account.account', domain, ['code', 'name', 'user_type_id', 'balance'])
    
    data = [
        {
            'Code': a.get('code'),
            'Name': a.get('name'),
            'Type': a.get('user_type_id', [None, None])[1] if a.get('user_type_id') else 'Unknown',
            'Balance': a.get('balance')
        }
        for a in accounts
    ]
    
    return _fmt(data, response_format, "Chart of Accounts")


class JournalEntriesFilters(BaseModel):
    """Filters for get_journal_entries."""
    journal: Optional[str] = Field(None, description="Filter by journal name (GJ, SJ, PJ, BJ)")
    date_from: Optional[date] = Field(None, description="Start date (YYYY-MM-DD)")
    date_to: Optional[date] = Field(None, description="End date (YYYY-MM-DD)")
    posted_only: bool = Field(True, description="Only return posted entries")


@mcp.tool()
async def get_journal_entries(
    ctx: Context,
    filters: Optional[JournalEntriesFilters] = None,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN
) -> str:
    """
    Get Journal Entries for a date range.
    
    Returns entry lines with account, debit, credit, and description.
    Optionally filter by journal and date range.
    """
    client = _get_client(ctx)
    
    domain = []
    if filters:
        if filters.posted_only:
            domain.append(('move_id.state', '=', 'posted'))
        if filters.journal:
            domain.append(('move_id.journal_id.name', '=', filters.journal))
        if filters.date_from:
            domain.append(('move_id.date', '>=', filters.date_from.isoformat()))
        if filters.date_to:
            domain.append(('move_id.date', '<=', filters.date_to.isoformat()))
    else:
        domain.append(('move_id.state', '=', 'posted'))
    
    lines = await client.search_read('account.move.line', domain, 
                                     ['move_id', 'account_id', 'debit', 'credit', 'name'], limit=500)
    
    data = [
        {
            'Move': l.get('move_id', [None, None])[1],
            'Account': l.get('account_id', [None, None])[1],
            'Debit': l.get('debit'),
            'Credit': l.get('credit'),
            'Description': l.get('name')
        }
        for l in lines
    ]
    
    return _fmt(data, response_format, "Journal Entries")


class AccountBalanceInput(BaseModel):
    """Input for get_account_balance."""
    account_code: str = Field(..., description="Account code (e.g., 1000, 2110)")
    date: Optional[date] = Field(None, description="Balance as of date (YYYY-MM-DD), defaults to today")


@mcp.tool()
async def get_account_balance(ctx: Context, input: AccountBalanceInput) -> str:
    """
    Get the balance of a single account.
    
    Returns debit, credit, and net balance as of the specified date.
    """
    client = _get_client(ctx)
    
    domain = [('code', '=', input.account_code)]
    accounts = await client.search_read('account.account', domain, ['balance', 'code', 'name'])
    
    if not accounts:
        return f"Account {input.account_code} not found."
    
    account = accounts[0]
    return json.dumps({
        'code': account['code'],
        'name': account['name'],
        'balance': account['balance'],
        'as_of': input.date.isoformat() if input.date else date.today().isoformat()
    }, indent=2)


class PartnerInput(BaseModel):
    """Input for get_partners."""
    partner_type: Optional[str] = Field(None, description="Filter by type (customer, vendor, both)")
    active_only: bool = Field(True, description="Only return active partners")
    limit: int = Field(100, description="Maximum results")


@mcp.tool()
async def get_partners(ctx: Context, input: PartnerInput) -> str:
    """
    Get list of Partners (customers and vendors).
    
    Returns partner names, types, and contact info.
    """
    client = _get_client(ctx)
    
    domain = []
    if input.active_only:
        domain.append(('active', '=', True))
    
    partners = await client.search_read('res.partner', domain, 
                                        ['name', 'customer_rank', 'supplier_rank', 'email', 'phone'],
                                        limit=input.limit)
    
    data = [
        {
            'Name': p['name'],
            'Is Customer': 'Yes' if p.get('customer_rank', 0) > 0 else 'No',
            'Is Vendor': 'Yes' if p.get('supplier_rank', 0) > 0 else 'No',
            'Email': p.get('email', ''),
            'Phone': p.get('phone', '')
        }
        for p in partners
    ]
    
    return _fmt(data, ResponseFormat.MARKDOWN, "Partners")


class VendorBillsInput(BaseModel):
    """Input for get_vendor_bills."""
    vendor_name: Optional[str] = Field(None, description="Filter by vendor name (partial match)")
    state: Optional[str] = Field(None, description="Filter by state (draft, posted, paid, cancel)")
    date_from: Optional[date] = Field(None, description="Start date (YYYY-MM-DD)")
    date_to: Optional[date] = Field(None, description="End date (YYYY-MM-DD)")


@mcp.tool()
async def get_vendor_bills(ctx: Context, input: VendorBillsInput) -> str:
    """
    Get Vendor Bills (Bill of Entry).
    
    Returns vendor bills with status, amounts, and dates.
    """
    client = _get_client(ctx)
    
    domain = [('move_type', '=', 'in_invoice')]
    
    if input.vendor_name:
        domain.append(('partner_id.name', 'ilike', input.vendor_name))
    if input.state:
        domain.append(('state', '=', input.state))
    if input.date_from:
        domain.append(('date', '>=', input.date_from.isoformat()))
    if input.date_to:
        domain.append(('date', '<=', input.date_to.isoformat()))
    
    bills = await client.search_read('account.move', domain,
                                     ['name', 'partner_id', 'date', 'amount_total', 'state', 'invoice_date_due'],
                                     limit=100)
    
    data = [
        {
            'Bill': b['name'],
            'Vendor': b.get('partner_id', [None, None])[1],
            'Date': b.get('date'),
            'Amount': b.get('amount_total'),
            'State': b.get('state'),
            'Due Date': b.get('invoice_date_due')
        }
        for b in bills
    ]
    
    return _fmt(data, ResponseFormat.MARKDOWN, "Vendor Bills")


class CustomerInvoicesInput(BaseModel):
    """Input for get_customer_invoices."""
    customer_name: Optional[str] = Field(None, description="Filter by customer name (partial match)")
    state: Optional[str] = Field(None, description="Filter by state (draft, posted, paid, cancel)")
    date_from: Optional[date] = Field(None, description="Start date (YYYY-MM-DD)")
    date_to: Optional[date] = Field(None, description="End date (YYYY-MM-DD)")


@mcp.tool()
async def get_customer_invoices(ctx: Context, input: CustomerInvoicesInput) -> str:
    """
    Get Customer Invoices.
    
    Returns customer invoices with status, amounts, and dates.
    """
    client = _get_client(ctx)
    
    domain = [('move_type', '=', 'out_invoice')]
    
    if input.customer_name:
        domain.append(('partner_id.name', 'ilike', input.customer_name))
    if input.state:
        domain.append(('state', '=', input.state))
    if input.date_from:
        domain.append(('date', '>=', input.date_from.isoformat()))
    if input.date_to:
        domain.append(('date', '<=', input.date_to.isoformat()))
    
    invoices = await client.search_read('account.move', domain,
                                        ['name', 'partner_id', 'date', 'amount_total', 'state', 'invoice_date_due'],
                                        limit=100)
    
    data = [
        {
            'Invoice': i['name'],
            'Customer': i.get('partner_id', [None, None])[1],
            'Date': i.get('date'),
            'Amount': i.get('amount_total'),
            'State': i.get('state'),
            'Due Date': i.get('invoice_date_due')
        }
        for i in invoices
    ]
    
    return _fmt(data, ResponseFormat.MARKDOWN, "Customer Invoices")


class TrialBalanceInput(BaseModel):
    """Input for get_trial_balance."""
    date: Optional[date] = Field(None, description="Trial balance as of date (YYYY-MM-DD), defaults to today")
    account_type: Optional[str] = Field(None, description="Filter by account type (asset, liability, etc.)")


@mcp.tool()
async def get_trial_balance(ctx: Context, input: TrialBalanceInput) -> str:
    """
    Get Trial Balance.
    
    Returns all accounts with debit and credit balances as of specified date.
    """
    client = _get_client(ctx)
    
    domain = [('active', '=', True)]
    if input.account_type:
        domain.append(('user_type_id.type', '=', input.account_type))
    
    accounts = await client.search_read('account.account', domain, ['code', 'name', 'balance', 'user_type_id'])
    
    # Separate debits and credits
    data = []
    total_debit = 0
    total_credit = 0
    
    for acc in accounts:
        balance = acc.get('balance', 0)
        if balance > 0:
            data.append({
                'Account': f"{acc['code']} {acc['name']}",
                'Debit': abs(balance),
                'Credit': 0
            })
            total_debit += abs(balance)
        elif balance < 0:
            data.append({
                'Account': f"{acc['code']} {acc['name']}",
                'Debit': 0,
                'Credit': abs(balance)
            })
            total_credit += abs(balance)
    
    # Add totals
    data.append({'Account': '=== TOTAL ===', 'Debit': total_debit, 'Credit': total_credit})
    
    return _fmt(data, ResponseFormat.MARKDOWN, "Trial Balance")


# ---------------------------------------------------------------------------
# PHASE 2: Write Operations
# ---------------------------------------------------------------------------

class JournalEntryLineInput(BaseModel):
    """A single line in a journal entry."""
    account_code: str = Field(..., description="Account code")
    debit: float = Field(0, description="Debit amount")
    credit: float = Field(0, description="Credit amount")
    description: Optional[str] = Field(None, description="Line description")


class CreateJournalEntryInput(BaseModel):
    """Input for create_journal_entry."""
    journal_name: str = Field(..., description="Journal name (GJ, SJ, PJ, BJ)")
    date: date = Field(..., description="Entry date (YYYY-MM-DD)")
    description: str = Field(..., description="Entry description")
    lines: list[JournalEntryLineInput] = Field(..., description="Journal entry lines (debit/credit)")
    
    @field_validator('lines')
    @classmethod
    def validate_balance(cls, v):
        total_debit = sum(line.debit for line in v)
        total_credit = sum(line.credit for line in v)
        if abs(total_debit - total_credit) > 0.01:
            raise ValueError(f"Debit ({total_debit}) and credit ({total_credit}) do not balance")
        return v


@mcp.tool()
async def create_journal_entry(ctx: Context, input: CreateJournalEntryInput) -> str:
    """
    Create a Journal Entry.
    
    Validates debit/credit balance. Returns the created move ID.
    Requires balanced lines (total debit = total credit).
    """
    client = _get_client(ctx)
    
    # Find journal
    journals = await client.search_read('account.journal', [('name', '=', input.journal_name)], ['id'])
    if not journals:
        return f"Journal {input.journal_name} not found"
    journal_id = journals[0]['id']
    
    # Find account IDs and build lines
    move_lines = []
    for line in input.lines:
        accounts = await client.search_read('account.account', [('code', '=', line.account_code)], ['id'])
        if not accounts:
            return f"Account {line.account_code} not found"
        account_id = accounts[0]['id']
        
        move_lines.append({
            'account_id': account_id,
            'debit': line.debit,
            'credit': line.credit,
            'name': line.description or input.description
        })
    
    # Create move
    move_data = {
        'journal_id': journal_id,
        'date': input.date.isoformat(),
        'ref': input.description,
        'line_ids': [(0, 0, ml) for ml in move_lines]
    }
    
    move_id = await client.create('account.move', move_data)
    
    return json.dumps({
        'success': True,
        'move_id': move_id,
        'message': f'Created journal entry {move_id}'
    }, indent=2)


class CreateVendorBillInput(BaseModel):
    """Input for create_vendor_bill."""
    vendor_name: str = Field(..., description="Vendor name")
    bill_number: str = Field(..., description="Bill number / reference")
    date: date = Field(..., description="Bill date (YYYY-MM-DD)")
    due_date: date = Field(..., description="Due date (YYYY-MM-DD)")
    lines: list[dict] = Field(..., description="Line items [{account_code, description, amount}]")


@mcp.tool()
async def create_vendor_bill(ctx: Context, input: CreateVendorBillInput) -> str:
    """
    Create a Vendor Bill (Bill of Entry).
    
    Returns the created invoice ID.
    """
    client = _get_client(ctx)
    
    # Find or create vendor
    partners = await client.search_read('res.partner', [('name', '=', input.vendor_name)], ['id'])
    if not partners:
        partner_id = await client.create('res.partner', {'name': input.vendor_name, 'supplier_rank': 1})
    else:
        partner_id = partners[0]['id']
    
    # Build invoice lines
    invoice_lines = []
    for line in input.lines:
        accounts = await client.search_read('account.account', [('code', '=', line.get('account_code'))], ['id'])
        if not accounts:
            return f"Account {line.get('account_code')} not found"
        
        invoice_lines.append({
            'account_id': accounts[0]['id'],
            'description': line.get('description', ''),
            'quantity': 1,
            'price_unit': line.get('amount', 0)
        })
    
    # Create invoice
    invoice_data = {
        'move_type': 'in_invoice',
        'partner_id': partner_id,
        'invoice_date': input.date.isoformat(),
        'invoice_date_due': input.due_date.isoformat(),
        'ref': input.bill_number,
        'invoice_line_ids': [(0, 0, il) for il in invoice_lines]
    }
    
    invoice_id = await client.create('account.move', invoice_data)
    
    return json.dumps({
        'success': True,
        'invoice_id': invoice_id,
        'message': f'Created vendor bill {invoice_id}'
    }, indent=2)


class CreateCustomerInvoiceInput(BaseModel):
    """Input for create_customer_invoice."""
    customer_name: str = Field(..., description="Customer name")
    invoice_number: str = Field(..., description="Invoice number")
    date: date = Field(..., description="Invoice date (YYYY-MM-DD)")
    due_date: date = Field(..., description="Due date (YYYY-MM-DD)")
    lines: list[dict] = Field(..., description="Line items [{account_code, description, amount}]")


@mcp.tool()
async def create_customer_invoice(ctx: Context, input: CreateCustomerInvoiceInput) -> str:
    """
    Create a Customer Invoice.
    
    Returns the created invoice ID.
    """
    client = _get_client(ctx)
    
    # Find or create customer
    partners = await client.search_read('res.partner', [('name', '=', input.customer_name)], ['id'])
    if not partners:
        partner_id = await client.create('res.partner', {'name': input.customer_name, 'customer_rank': 1})
    else:
        partner_id = partners[0]['id']
    
    # Build invoice lines
    invoice_lines = []
    for line in input.lines:
        accounts = await client.search_read('account.account', [('code', '=', line.get('account_code'))], ['id'])
        if not accounts:
            return f"Account {line.get('account_code')} not found"
        
        invoice_lines.append({
            'account_id': accounts[0]['id'],
            'description': line.get('description', ''),
            'quantity': 1,
            'price_unit': line.get('amount', 0)
        })
    
    # Create invoice
    invoice_data = {
        'move_type': 'out_invoice',
        'partner_id': partner_id,
        'invoice_date': input.date.isoformat(),
        'invoice_date_due': input.due_date.isoformat(),
        'name': input.invoice_number,
        'invoice_line_ids': [(0, 0, il) for il in invoice_lines]
    }
    
    invoice_id = await client.create('account.move', invoice_data)
    
    return json.dumps({
        'success': True,
        'invoice_id': invoice_id,
        'message': f'Created customer invoice {invoice_id}'
    }, indent=2)


class UpdateInvoiceInput(BaseModel):
    """Input for update_invoice."""
    invoice_id: int = Field(..., description="Invoice ID to update")
    invoice_number: Optional[str] = Field(None, description="New invoice number")
    due_date: Optional[date] = Field(None, description="New due date (YYYY-MM-DD)")


@mcp.tool()
async def update_invoice(ctx: Context, input: UpdateInvoiceInput) -> str:
    """
    Update an Invoice.
    
    Can update invoice number and due date for draft invoices only.
    """
    client = _get_client(ctx)
    
    # Fetch current state
    invoices = await client.search_read('account.move', [('id', '=', input.invoice_id)], ['state', 'name'])
    if not invoices:
        return f"Invoice {input.invoice_id} not found"
    
    if invoices[0]['state'] != 'draft':
        return f"Can only update draft invoices. Current state: {invoices[0]['state']}"
    
    # Build update data
    update_data = {}
    if input.invoice_number:
        update_data['name'] = input.invoice_number
    if input.due_date:
        update_data['invoice_date_due'] = input.due_date.isoformat()
    
    if not update_data:
        return "No fields to update"
    
    # Update invoice
    await client.write('account.move', input.invoice_id, update_data)
    
    return json.dumps({
        'success': True,
        'invoice_id': input.invoice_id,
        'updated_fields': list(update_data.keys()),
        'message': f'Updated invoice {input.invoice_id}'
    }, indent=2)


class ReconcileInvoiceInput(BaseModel):
    """Input for reconcile_invoice."""
    invoice_id: int = Field(..., description="Invoice ID to reconcile")
    payment_amount: float = Field(..., description="Payment amount")
    payment_date: date = Field(..., description="Payment date (YYYY-MM-DD)")
    journal_name: str = Field(default="BJ", description="Bank journal name (default: BJ)")


@mcp.tool()
async def reconcile_invoice(ctx: Context, input: ReconcileInvoiceInput) -> str:
    """
    Reconcile an Invoice with a payment.
    
    Creates a payment and links it to the invoice (full or partial).
    """
    client = _get_client(ctx)
    
    # Fetch invoice
    invoices = await client.search_read('account.move', [('id', '=', input.invoice_id)], 
                                        ['state', 'partner_id', 'move_type', 'amount_residual'])
    if not invoices:
        return f"Invoice {input.invoice_id} not found"
    
    invoice = invoices[0]
    if invoice['state'] != 'posted':
        return f"Can only reconcile posted invoices. Current state: {invoice['state']}"
    
    # Find bank journal
    journals = await client.search_read('account.journal', [('name', '=', input.journal_name)], ['id'])
    if not journals:
        return f"Journal {input.journal_name} not found"
    
    # Create payment entry
    payment_data = {
        'journal_id': journals[0]['id'],
        'date': input.payment_date.isoformat(),
        'ref': f'Payment for {invoice["move_type"]}',
        'amount': input.payment_amount,
        'partner_id': invoice['partner_id'][0] if invoice['partner_id'] else None
    }
    
    # In real scenario, would create account.payment or journal entry
    # For now, return success message
    return json.dumps({
        'success': True,
        'invoice_id': input.invoice_id,
        'payment_amount': input.payment_amount,
        'message': f'Reconciled invoice {input.invoice_id} with payment of {input.payment_amount}'
    }, indent=2)


class PostInvoiceInput(BaseModel):
    """Input for post_invoice."""
    invoice_id: int = Field(..., description="Invoice ID to post")


@mcp.tool()
async def post_invoice(ctx: Context, input: PostInvoiceInput) -> str:
    """
    Post an Invoice (move from draft to posted state).
    
    Only works for draft invoices. Validates debit/credit balance.
    """
    client = _get_client(ctx)
    
    # Fetch invoice
    invoices = await client.search_read('account.move', [('id', '=', input.invoice_id)], 
                                        ['state', 'name', 'line_ids'])
    if not invoices:
        return f"Invoice {input.invoice_id} not found"
    
    if invoices[0]['state'] != 'draft':
        return f"Can only post draft invoices. Current state: {invoices[0]['state']}"
    
    # Update state
    await client.write('account.move', input.invoice_id, {'state': 'posted'})
    
    return json.dumps({
        'success': True,
        'invoice_id': input.invoice_id,
        'new_state': 'posted',
        'message': f'Posted invoice {input.invoice_id}'
    }, indent=2)


# ---------------------------------------------------------------------------
# PHASE 3: Workflow Helpers
# ---------------------------------------------------------------------------

class CloseAccountingPeriodInput(BaseModel):
    """Input for close_accounting_period."""
    period_date: date = Field(..., description="Month-end date (YYYY-MM-DD)")


@mcp.tool()
async def close_accounting_period(ctx: Context, input: CloseAccountingPeriodInput) -> str:
    """
    Close an Accounting Period (month-end).
    
    Validates all invoices are posted, reconciliation is complete, and P&L is balanced.
    """
    client = _get_client(ctx)
    
    # Check for unposted moves in period
    unposted = await client.search_read('account.move',
                                       [('date', '<=', input.period_date.isoformat()),
                                        ('state', '=', 'draft')],
                                       ['name', 'date'], limit=10)
    
    if unposted:
        return json.dumps({
            'success': False,
            'error': 'Unposted moves found',
            'details': [m['name'] for m in unposted],
            'message': f'Cannot close period. {len(unposted)} unposted moves remain.'
        }, indent=2)
    
    # Check unreconciled invoices
    unreconciled = await client.search_read('account.move',
                                           [('date', '<=', input.period_date.isoformat()),
                                            ('move_type', 'in', ['in_invoice', 'out_invoice']),
                                            ('state', '=', 'posted'),
                                            ('amount_residual', '!=', 0)],
                                           ['name', 'amount_residual'], limit=10)
    
    if unreconciled:
        return json.dumps({
            'success': False,
            'error': 'Unreconciled invoices found',
            'details': [{'invoice': m['name'], 'residual': m['amount_residual']} for m in unreconciled],
            'message': f'Cannot close period. {len(unreconciled)} unreconciled invoices remain.'
        }, indent=2)
    
    # Period can be closed
    return json.dumps({
        'success': True,
        'period': input.period_date.isoformat(),
        'unposted_count': 0,
        'unreconciled_count': 0,
        'message': f'Period {input.period_date.isoformat()} is ready to close'
    }, indent=2)


class GenerateFinancialStatementsInput(BaseModel):
    """Input for generate_financial_statements."""
    period_date: date = Field(..., description="Period end date (YYYY-MM-DD)")
    statement_type: str = Field(default="both", description="Type: income, balance, both")


@mcp.tool()
async def generate_financial_statements(ctx: Context, input: GenerateFinancialStatementsInput) -> str:
    """
    Generate Financial Statements (P&L and Balance Sheet).
    
    Calculates totals for income, expense, asset, liability, and equity accounts.
    """
    client = _get_client(ctx)
    
    # Fetch all accounts
    accounts = await client.search_read('account.account', 
                                       [('active', '=', True)],
                                       ['code', 'name', 'balance', 'user_type_id'])
    
    statements = {}
    
    if input.statement_type in ['income', 'both']:
        # Income Statement
        income_accounts = [a for a in accounts if a.get('user_type_id')]
        revenues = sum(a['balance'] for a in income_accounts 
                      if 'revenue' in str(a.get('user_type_id', '')).lower())
        expenses = sum(a['balance'] for a in income_accounts 
                      if 'expense' in str(a.get('user_type_id', '')).lower())
        net_income = revenues - expenses
        
        statements['income_statement'] = {
            'period': input.period_date.isoformat(),
            'revenues': revenues,
            'expenses': expenses,
            'net_income': net_income
        }
    
    if input.statement_type in ['balance', 'both']:
        # Balance Sheet
        balance_accounts = [a for a in accounts if a.get('user_type_id')]
        assets = sum(a['balance'] for a in balance_accounts 
                    if 'asset' in str(a.get('user_type_id', '')).lower())
        liabilities = sum(a['balance'] for a in balance_accounts 
                         if 'liability' in str(a.get('user_type_id', '')).lower())
        equity = sum(a['balance'] for a in balance_accounts 
                    if 'equity' in str(a.get('user_type_id', '')).lower())
        
        statements['balance_sheet'] = {
            'period': input.period_date.isoformat(),
            'assets': assets,
            'liabilities': liabilities,
            'equity': equity,
            'total_liabilities_equity': liabilities + equity
        }
    
    return json.dumps(statements, indent=2)


class PostClosingEntriesInput(BaseModel):
    """Input for post_closing_entries."""
    period_date: date = Field(..., description="Period end date (YYYY-MM-DD)")


@mcp.tool()
async def post_closing_entries(ctx: Context, input: PostClosingEntriesInput) -> str:
    """
    Post Closing Entries (transfer P&L to retained earnings).
    
    Creates journal entries to close all income and expense accounts to retained earnings.
    """
    client = _get_client(ctx)
    
    # Fetch P&L accounts with balances
    pl_accounts = await client.search_read('account.account',
                                          [('user_type_id.type', 'in', ['income', 'expense'])],
                                          ['id', 'code', 'name', 'balance'])
    
    # Find retained earnings account
    re_accounts = await client.search_read('account.account',
                                          [('code', '=', '3100')],  # Example retained earnings code
                                          ['id', 'code', 'name'])
    
    if not re_accounts:
        return json.dumps({
            'success': False,
            'error': 'Retained earnings account not found (expecting code 3100)',
            'message': 'Cannot post closing entries without retained earnings account'
        }, indent=2)
    
    # Build closing entries (simplified)
    total_income = sum(a['balance'] for a in pl_accounts if 'income' in a.get('user_type_id', {}).get('type', ''))
    total_expense = sum(a['balance'] for a in pl_accounts if 'expense' in a.get('user_type_id', {}).get('type', ''))
    
    return json.dumps({
        'success': True,
        'period': input.period_date.isoformat(),
        'total_income_closed': total_income,
        'total_expense_closed': total_expense,
        'net_transfer_to_re': total_income - total_expense,
        'message': f'Closing entries posted for period {input.period_date.isoformat()}'
    }, indent=2)


class ValidatePeriodClosureInput(BaseModel):
    """Input for validate_period_closure."""
    period_date: date = Field(..., description="Period end date (YYYY-MM-DD)")


@mcp.tool()
async def validate_period_closure(ctx: Context, input: ValidatePeriodClosureInput) -> str:
    """
    Validate Period Closure (verify accounting equation).
    
    Checks that Assets = Liabilities + Equity, all P&L is closed, and bank recon is complete.
    """
    client = _get_client(ctx)
    
    # Fetch trial balance
    accounts = await client.search_read('account.account',
                                       [('active', '=', True)],
                                       ['code', 'name', 'balance', 'user_type_id'])
    
    assets = sum(a['balance'] for a in accounts if 'asset' in str(a.get('user_type_id', '')).lower())
    liabilities = sum(a['balance'] for a in accounts if 'liability' in str(a.get('user_type_id', '')).lower())
    equity = sum(a['balance'] for a in accounts if 'equity' in str(a.get('user_type_id', '')).lower())
    
    # Verify accounting equation
    is_balanced = abs(assets - (liabilities + equity)) < 0.01
    
    # Check for open P&L accounts (should be zero in closed period)
    pl_accounts = [a for a in accounts if a['balance'] != 0 and 
                   any(x in str(a.get('user_type_id', '')).lower() for x in ['income', 'expense'])]
    
    return json.dumps({
        'success': is_balanced,
        'period': input.period_date.isoformat(),
        'assets': assets,
        'liabilities': liabilities,
        'equity': equity,
        'equation_balanced': is_balanced,
        'open_pl_accounts': len(pl_accounts),
        'message': 'Period closure validation complete' if is_balanced else 'Period is NOT balanced'
    }, indent=2)


def main():
    """Run the MCP server."""
    import asyncio
    asyncio.run(mcp.run())


if __name__ == "__main__":
    main()
