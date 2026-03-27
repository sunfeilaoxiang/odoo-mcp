"""Odoo JSON-RPC client for MCP server integration."""

import asyncio
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel


class OdooClientError(Exception):
    """Base exception for Odoo client errors."""

    pass


class OdooClient:
    """JSON-RPC client for Odoo 18 API."""

    def __init__(
        self,
        url: str,
        db: str,
        username: str,
        password: str,
        timeout: float = 30.0,
    ):
        """Initialize Odoo client.

        Args:
            url: Odoo instance URL
            db: Database name
            username: Login username
            password: Login password
            timeout: Request timeout in seconds
        """
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self.password = password
        self.timeout = timeout
        self.uid: Optional[int] = None
        self.session_id: Optional[str] = None
        self.client = httpx.AsyncClient(timeout=timeout)

    async def connect(self) -> None:
        """Authenticate with Odoo server."""
        try:
            result = await self._rpc_call("web/session/authenticate", {
                "db": self.db,
                "login": self.username,
                "password": self.password,
                "type": "password",
            })

            if not result.get("uid"):
                raise OdooClientError("Authentication failed")

            self.uid = result["uid"]
            self.session_id = result.get("session_id")
        except Exception as e:
            await self.close()
            raise OdooClientError(f"Connection failed: {str(e)}")

    async def close(self) -> None:
        """Close the client connection."""
        await self.client.aclose()

    async def _rpc_call(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make a JSON-RPC call to Odoo.

        Args:
            method: RPC method name
            params: Method parameters

        Returns:
            Response data

        Raises:
            OdooClientError: If RPC call fails
        """
        if params is None:
            params = {}

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }

        try:
            response = await self.client.post(
                f"{self.url}/jsonrpc",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

            if "error" in data:
                error = data["error"]
                raise OdooClientError(
                    f"RPC Error: {error.get('message', 'Unknown error')}"
                )

            return data.get("result", {})
        except httpx.HTTPError as e:
            raise OdooClientError(f"HTTP Error: {str(e)}")
        except Exception as e:
            raise OdooClientError(f"RPC call failed: {str(e)}")

    async def search_read(
        self,
        model: str,
        domain: List[Any],
        fields: Optional[List[str]] = None,
        limit: int = 80,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Search and read records.

        Args:
            model: Model name (e.g., 'account.move')
            domain: Search domain
            fields: Fields to return
            limit: Record limit
            offset: Record offset

        Returns:
            List of records
        """
        if not self.uid:
            raise OdooClientError("Not connected")

        result = await self._rpc_call("call", {
            "service": "object",
            "method": "execute_kw",
            "args": [
                self.db,
                self.uid,
                self.password,
                model,
                "search_read",
                [domain],
                {
                    "fields": fields or [],
                    "limit": limit,
                    "offset": offset,
                },
            ],
        })

        return result if isinstance(result, list) else []

    async def create(
        self,
        model: str,
        values: Dict[str, Any],
    ) -> int:
        """Create a record.

        Args:
            model: Model name
            values: Record values

        Returns:
            ID of created record
        """
        if not self.uid:
            raise OdooClientError("Not connected")

        result = await self._rpc_call("call", {
            "service": "object",
            "method": "execute_kw",
            "args": [
                self.db,
                self.uid,
                self.password,
                model,
                "create",
                [values],
            ],
        })

        if not isinstance(result, int):
            raise OdooClientError("Create failed: invalid response")

        return result

    async def write(
        self,
        model: str,
        ids: List[int],
        values: Dict[str, Any],
    ) -> bool:
        """Update records.

        Args:
            model: Model name
            ids: Record IDs to update
            values: Values to update

        Returns:
            True if successful
        """
        if not self.uid:
            raise OdooClientError("Not connected")

        result = await self._rpc_call("call", {
            "service": "object",
            "method": "execute_kw",
            "args": [
                self.db,
                self.uid,
                self.password,
                model,
                "write",
                [ids, values],
            ],
        })

        return result is True

    async def unlink(
        self,
        model: str,
        ids: List[int],
    ) -> bool:
        """Delete records.

        Args:
            model: Model name
            ids: Record IDs to delete

        Returns:
            True if successful
        """
        if not self.uid:
            raise OdooClientError("Not connected")

        result = await self._rpc_call("call", {
            "service": "object",
            "method": "execute_kw",
            "args": [
                self.db,
                self.uid,
                self.password,
                model,
                "unlink",
                [ids],
            ],
        })

        return result is True

    async def fields_get(
        self,
        model: str,
        fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Get field definitions.

        Args:
            model: Model name
            fields: Specific fields to get

        Returns:
            Field definitions
        """
        if not self.uid:
            raise OdooClientError("Not connected")

        result = await self._rpc_call("call", {
            "service": "object",
            "method": "execute_kw",
            "args": [
                self.db,
                self.uid,
                self.password,
                model,
                "fields_get",
                [] if not fields else [fields],
                {},
            ],
        })

        return result if isinstance(result, dict) else {}


async def client_from_env() -> OdooClient:
    """Create client from environment variables.

    Returns:
        Configured OdooClient

    Raises:
        OdooClientError: If environment variables are missing
    """
    import os

    url = os.getenv("ODOO_URL")
    db = os.getenv("ODOO_DB")
    username = os.getenv("ODOO_USER")
    password = os.getenv("ODOO_PASSWORD")

    if not all([url, db, username, password]):
        raise OdooClientError("Missing Odoo environment variables")

    return OdooClient(
        url=url,
        db=db,
        username=username,
        password=password,
    )
