"""
LSP Server for C/C++ projects using multilspy with Flask HTTP interface
"""

import os
import time
import signal
import logging
import argparse
import subprocess
import contextlib
from flask import Flask, request, jsonify
from multilspy.multilspy_config import MultilspyConfig, Language
from multilspy.multilspy_logger import MultilspyLogger
from multilspy import SyncLanguageServer


PORT = 28364


class LSPRequestHandler:
    """
    Handler for LSP requests
    """

    def __init__(self, repo_dir: str):
        self.repo_dir = repo_dir
        self.lsp_instance = None

    @contextlib.contextmanager
    def start_lsp(self):
        """Initialize the LSP instance"""

        print(f"Initializing LSP instance ...")

        config = MultilspyConfig(
            code_language=Language.CPP,
            trace_lsp_communication=True,
        )

        logger = MultilspyLogger()
        logger.logger.setLevel(logging.DEBUG)

        self.lsp_instance = SyncLanguageServer.create(
            config=config,
            logger=logger,
            repository_root_path=self.repo_dir,
        )

        # Start the server
        with self.lsp_instance.start_server():
            time.sleep(6)  # Wait for server to initialize
            yield self

    def handle_request(self, request_data: dict) -> dict:
        """Handle LSP request and return response"""

        def _check_is_not_none(value, name: str):
            if value is None:
                raise ValueError(f"{name} must be provided")

        try:
            operation = request_data.get("operation")
            file_path = request_data.get("file_path")
            line = request_data.get("line")
            column = request_data.get("column")
            query = request_data.get("query")

            _abs_file_path = file_path
            if not os.path.isabs(file_path):
                _abs_file_path = os.path.join(self.repo_dir, file_path)
            if not os.path.isfile(_abs_file_path):
                return {
                    "type": "error",
                    "message": f"File '{_abs_file_path}' does not exist",
                }
            file_path = os.path.relpath(_abs_file_path, start=self.repo_dir)
            del _abs_file_path

            if (line is not None and line < 0) or (column is not None and column < 0):
                return {
                    "type": "error",
                    "message": "Line and column must be non-negative integers",
                }

            if operation == "find_definition":
                _check_is_not_none(file_path, "file_path")
                _check_is_not_none(line, "line")
                _check_is_not_none(column, "column")
                result = self.lsp_instance.request_definition(file_path, line, column)
                return {"type": operation, "result": result}
            elif operation == "find_references":
                _check_is_not_none(file_path, "file_path")
                _check_is_not_none(line, "line")
                _check_is_not_none(column, "column")
                result = self.lsp_instance.request_references(file_path, line, column)
                return {"type": operation, "result": result}
            elif operation == "document_symbols":
                _check_is_not_none(file_path, "file_path")
                result = self.lsp_instance.request_document_symbols(file_path)
                return {"type": operation, "result": result}
            elif operation == "workspace_symbol":
                _check_is_not_none(query, "query")
                result = self.lsp_instance.request_workspace_symbol(query)
                return {"type": operation, "result": result}
            else:
                return {"type": "error", "message": f"Unknown operation: {operation}"}
        except Exception as e:
            return {"type": "error", "message": f"Request failed: {str(e)}"}


def create_compile_commands(repo_dir: str) -> bool:
    """
    Generate compile_commands.json using the provided script
    """

    try:
        # Execute the compile database generation script
        print(f"Running compile database generation script ...")
        script_path = "/static_analysis_tools/gen_compile_db.sh"
        result = subprocess.run(
            ["bash", script_path, repo_dir],
            capture_output=True,
            text=True,
            cwd=repo_dir,
        )

        if result.returncode != 0:
            print(f"Error generating compile commands: {result.stderr}")
            return False

        print("Successfully generated compile_commands.json")
        print("==============")
        print(result.stdout)
        print("==============")
        return True
    except Exception as e:
        print(f"Failed to generate compile commands: {e}")
        return False


def start_lsp_server(repo_dir: str):
    """
    Start LSP server for C/C++ project
    """
    # Generate compile database first
    print(f"Generating compile_commands.json ...")
    if not create_compile_commands(repo_dir):
        raise RuntimeError("Failed to generate compile_commands.json")

    # Create LSP handler
    print(f"Initializing LSP handler ...")
    lsp_handler = LSPRequestHandler(repo_dir)

    # Create Flask app
    print(f"Initializing Flask app ...")
    app = Flask(__name__)

    @app.route("/health", methods=["POST"])
    def health_check():
        """Health check endpoint"""
        return jsonify({"type": "health", "result": "running"})

    @app.route("/exit", methods=["POST"])
    def exit_server():
        """Exit the server"""
        print("Exiting LSP server...")
        os.kill(os.getpid(), signal.SIGINT)
        return jsonify({"type": "exit", "result": "exited"})

    @app.route("/lsp", methods=["POST"])
    def handle_lsp_request():
        """Handle LSP requests"""
        try:
            request_data = request.get_json()
            if not request_data:
                return (
                    jsonify({"type": "error", "message": "No JSON data provided"}),
                    400,
                )

            response_data = lsp_handler.handle_request(request_data)
            return jsonify(response_data)
        except Exception as e:
            return jsonify({"type": "error", "message": str(e)}), 500

    print(f"Starting LSP HTTP server on port {PORT}...")
    print(f"Repository root: {repo_dir}")
    print("Server is running. Press Ctrl+C to stop.")

    with lsp_handler.start_lsp():
        app.run(host="localhost", port=PORT, debug=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo_dir", type=str, required=True)
    args = parser.parse_args()

    repo_dir = os.path.abspath(args.repo_dir)
    print(f"Repository directory: {repo_dir}")
    if not os.path.isdir(repo_dir):
        raise ValueError(f"Directory '{repo_dir}' does not exist")

    start_lsp_server(repo_dir)


if __name__ == "__main__":
    main()
