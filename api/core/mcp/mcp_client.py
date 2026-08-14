import logging
import re
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from types import TracebackType
from typing import Any

from flask import has_request_context, request

from core.entities.mcp_provider import MCPTransport
from core.mcp.client.sse_client import sse_client
from core.mcp.client.streamable_client import streamablehttp_client
from core.mcp.error import MCPAuthError, MCPConnectionError
from core.mcp.session.client_session import ClientSession
from core.mcp.types import CallToolResult, Tool

logger = logging.getLogger(__name__)


class MCPClient:
    def __init__(
        self,
        server_url: str,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        sse_read_timeout: float | None = None,
        transport: MCPTransport | None = None,
    ):
        self.server_url = server_url
        self.headers = headers.copy() if headers else {}
        self.timeout = timeout
        self.sse_read_timeout = sse_read_timeout
        self.transport = MCPTransport(transport) if transport else None
        self.selected_transport: MCPTransport | None = None

        # Substitute placeholders with incoming request headers if in a request context
        if has_request_context() and self.headers:
            pattern = re.compile(r"\{\{\s*request\.headers?\.(.+?)\s*\}\}", re.IGNORECASE)
            for key, value in list(self.headers.items()):
                if isinstance(value, str):

                    def replace_func(match):
                        header_name = match.group(1)
                        return request.headers.get(header_name, "")

                    self.headers[key] = pattern.sub(replace_func, value)

        # Initialize session and client objects
        self._session: ClientSession | None = None
        self._exit_stack = ExitStack()
        self._initialized = False

    def __enter__(self):
        self._initialize()
        self._initialized = True
        return self

    def __exit__(self, exc_type: type | None, exc_value: BaseException | None, traceback: TracebackType | None):
        self.cleanup()

    def _initialize(self):
        """Initialize a connection using the saved transport when available."""
        if self.transport == MCPTransport.STREAMABLE_HTTP:
            self._connect_streamable_http()
            return

        try:
            self._connect_sse()
        except MCPAuthError:
            raise
        except (MCPConnectionError, ValueError):
            self._exit_stack.close()
            self._exit_stack = ExitStack()
            logger.debug("MCP connection failed with SSE, falling back to Streamable HTTP.")
            self._connect_streamable_http()

    def _connect_sse(self) -> None:
        self.connect_server(sse_client, "sse")
        self.transport = MCPTransport.SSE
        self.selected_transport = MCPTransport.SSE

    def _connect_streamable_http(self) -> None:
        self.connect_server(streamablehttp_client, "mcp")
        self.transport = MCPTransport.STREAMABLE_HTTP
        self.selected_transport = MCPTransport.STREAMABLE_HTTP

    def connect_server(self, client_factory: Callable[..., AbstractContextManager[Any]], method_name: str) -> None:
        """
        Connect to the MCP server using streamable http or sse.
        Default to streamable http.
        Args:
            client_factory: The client factory to use(streamablehttp_client or sse_client).
            method_name: The method name to use(mcp or sse).
        """
        streams_context = client_factory(
            url=self.server_url,
            headers=self.headers,
            timeout=self.timeout,
            sse_read_timeout=self.sse_read_timeout,
        )

        # Use exit_stack to manage context managers properly
        if method_name == "mcp":
            read_stream, write_stream, _ = self._exit_stack.enter_context(streams_context)
            streams = (read_stream, write_stream)
        else:  # sse_client
            streams = self._exit_stack.enter_context(streams_context)

        session_context = ClientSession(*streams)
        self._session = self._exit_stack.enter_context(session_context)
        self._session.initialize()

    def list_tools(self) -> list[Tool]:
        """List available tools from the MCP server"""
        if not self._session:
            raise ValueError("Session not initialized.")
        response = self._session.list_tools()
        return response.tools

    def invoke_tool(self, tool_name: str, tool_args: dict[str, Any]) -> CallToolResult:
        """Call a tool"""
        if not self._session:
            raise ValueError("Session not initialized.")
        return self._session.call_tool(tool_name, tool_args)

    def cleanup(self):
        """Clean up resources"""
        try:
            # ExitStack will handle proper cleanup of all managed context managers
            self._exit_stack.close()
        except Exception as e:
            logger.exception("Error during cleanup")
            raise ValueError(f"Error during cleanup: {e}")
        finally:
            self._session = None
            self._initialized = False
