import argparse
from pathlib import Path

from .storage import read_config


def main():
    parser = argparse.ArgumentParser(description="FleetScope historical pickup forecasting")
    parser.add_argument("command", choices=["download", "prepare", "evaluate", "build", "serve"])
    parser.add_argument("--config", type=Path, default=Path("configs/starter.json"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--delay", type=int, choices=range(25), help="Observation delay in hours")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--run", type=Path, help="Serve a specific packaged run")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(root, args.run), host="127.0.0.1", port=args.port)
        return
    config_path = args.config if args.config.is_absolute() else root / args.config
    config = read_config(config_path)
    if args.delay is not None:
        config["observation_delay_hours"] = args.delay
    if args.command in ("download", "build"):
        from .download import download_sources

        download_sources(config, root)
    if args.command in ("prepare", "build"):
        from .data import prepare

        prepare(config, root)
    if args.command in ("evaluate", "build"):
        from .evaluation import evaluate

        evaluate(config, root)


if __name__ == "__main__":
    main()
