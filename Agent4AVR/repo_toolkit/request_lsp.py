#!/usr/bin/env python3
"""
LSP Client for requesting find definition or find references via HTTP
"""

import json
import requests
import argparse

from lsp_server import PORT


def send_lsp_request(
    server_host: str,
    operation: str,
    file_path: str,
    line: int,
    column: int,
    query: str,
) -> str:
    """
    Send LSP request to the server
    """

    try:
        # Prepare request data
        request_data = {
            "operation": operation,
            "file_path": file_path,
            "line": line,
            "column": column,
            "query": query,
        }

        # Send POST request to the LSP server
        response = requests.post(f"http://{server_host}:{PORT}/lsp", json=request_data)

        if response.status_code == 200:
            return json.dumps(response.json(), indent=2)
        else:
            return json.dumps(
                {
                    "type": "error",
                    "message": f"Server returned status code: {response.status_code}",
                },
                indent=2,
            )

    except requests.exceptions.RequestException as e:
        return json.dumps(
            {"type": "error", "message": f"Failed to connect to server: {e}"}, indent=2
        )


def health_check(server_host: str) -> str:
    """Health check request"""

    try:
        response = requests.post(f"http://{server_host}:{PORT}/health")

        if response.status_code == 200:
            return json.dumps(response.json(), indent=2)
        else:
            return json.dumps(
                {
                    "type": "error",
                    "message": f"Health check failed: {response.status_code}",
                },
                indent=2,
            )
    except requests.exceptions.RequestException as e:
        return json.dumps(
            {"type": "error", "message": f"Failed to connect for health check: {e}"},
            indent=2,
        )


def exit_server(server_host: str) -> str:
    """Exit server request"""

    try:
        response = requests.post(f"http://{server_host}:{PORT}/exit")
        return json.dumps(response.json(), indent=2)
    except requests.exceptions.RequestException as e:
        return json.dumps({"type": "exit", "result": "exited"}, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost", help="Host")
    parser.add_argument("--file", type=str, help="File path")
    parser.add_argument("--line", type=int, help="Line number (0-based)")
    parser.add_argument("--column", type=int, help="Column number (0-based)")
    parser.add_argument("--query", type=str, help="Query for workspace symbol")
    parser.add_argument("--operation", required=True)

    args = parser.parse_args()

    if args.operation == "health":
        result = health_check(args.host)
    elif args.operation == "exit":
        result = exit_server(args.host)
    elif args.operation in [
        "find_definition",
        "find_references",
        "document_symbols",
        "workspace_symbol",
    ]:
        result = send_lsp_request(
            server_host=args.host,
            operation=args.operation,
            file_path=args.file,
            line=args.line,
            column=args.column,
            query=args.query,
        )
    else:
        raise ValueError(f"Unknown operation: {args.operation}")

    print(result)


if __name__ == "__main__":
    main()
