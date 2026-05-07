"""
Entry point for the JUnit Generator Pipeline API server.

Usage:
    python api_server.py                    # default: localhost:8000
    python api_server.py --port 9000
    python api_server.py --host 0.0.0.0    # expose on all interfaces

Swagger UI:  http://localhost:8000/docs
ReDoc:       http://localhost:8000/redoc
"""
import typer
import uvicorn

def main(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Bind host"),
    port: int = typer.Option(8000, "--port", "-p", help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev mode)"),
    workers: int = typer.Option(1, "--workers", "-w", help="Number of worker processes"),
):
    """Start the JUnit Generator Pipeline REST API server."""
    print(f"\n  JUnit Generator Pipeline API")
    print(f"  ─────────────────────────────")
    print(f"  Swagger UI : http://{host}:{port}/docs")
    print(f"  ReDoc      : http://{host}:{port}/redoc")
    print(f"  Health     : http://{host}:{port}/api/health\n")

    uvicorn.run(
        "api.server:app",
        host=host,
        port=port,
        reload=reload,
        workers=workers,
        log_level="info",
    )

if __name__ == "__main__":
    typer.run(main)
