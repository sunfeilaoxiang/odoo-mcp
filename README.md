# Odoo 18 MCP Server

Full read/write access to Odoo 18 Enterprise Accounting via JSON-RPC using the Model Context Protocol (MCP).

## Overview

This MCP server provides 17 tools for comprehensive Odoo 18 accounting operations, organized in three phases:

- **Phase 1**: Read-only queries for financial visibility
- **Phase 2**: Write operations for creating and updating records
- **Phase 3**: Workflow helpers for month-end closing procedures

## Installation

1. Clone the repository:
```bash
git clone https://github.com/sunfeilaoxiang/odoo-mcp.git
cd odoo-mcp
```

2. Install the package:
```bash
pip install -e .
```

3. Configure environment variables:
```bash
cp .env.example .env
# Edit .env with your Odoo connection details
```

## Configuration

Set the following environment variables in `.env`:

- `ODOO_URL`: Your Odoo instance URL (e.g., `https://your-odoo.com`)
- `ODOO_DB`: Database name
- `ODOO_USER`: Login username
- `ODOO_PASSWORD`: Login password

## Usage

Start the MCP server:
```bash
odoo-mcp
```

### Phase 1: Read-Only Queries

1. **get_chart_of_accounts** - Retrieve the complete chart of accounts
2. **get_journal_entries** - Query journal entries with filtering
3. **get_account_balance** - Get account balances as of a date
4. **get_partners** - List customers and vendors
5. **get_vendor_bills** - Query vendor bills
6. **get_customer_invoices** - Query customer invoices
7. **get_trial_balance** - Generate trial balance report

### Phase 2: Write Operations

8. **create_journal_entry** - Create manual journal entries
9. **create_vendor_bill** - Create vendor bills from POs
10. **create_customer_invoice** - Create customer invoices from orders
11. **update_invoice** - Update invoice line items
12. **reconcile_invoice** - Mark invoices as paid
13. **post_invoice** - Post invoices to accounting

### Phase 3: Workflow Helpers

14. **close_accounting_period** - Lock accounting period
15. **generate_financial_statements** - Create P&L and balance sheet
16. **post_closing_entries** - Create closing entries
17. **validate_period_closure** - Verify period closure readiness

## Architecture

The server uses:

- **FastMCP**: Python SDK for MCP servers
- **httpx**: Async HTTP client for JSON-RPC communication
- **Pydantic**: Data validation and serialization
- **python-dotenv**: Environment configuration

## Error Handling

The OdooClient class provides structured error handling:

- `OdooClientError`: Base exception for all Odoo API errors
- JSON-RPC error responses are parsed and wrapped in exceptions
- Comprehensive logging for debugging

## Development

The project structure:

```
odoo_mcp/
├── __init__.py       # Package metadata
├── client.py         # OdooClient and JSON-RPC implementation
└── server.py         # FastMCP server with 17 tools
```

## Security Notes

- Never commit `.env` files with real credentials
- Use OAuth or SSO when available
- Implement proper access controls in your Odoo instance
- Audit all API calls through Odoo's audit trail

## Contributing

Contributions are welcome. Please ensure:

- Code follows PEP 8 style guidelines
- All new tools include proper error handling
- Documentation is updated for new features

## License

MIT License - See LICENSE file for details

## Support

For issues and questions, please use the GitHub issues page.
