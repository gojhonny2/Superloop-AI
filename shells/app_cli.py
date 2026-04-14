import argparse
import asyncio
import os

from dotenv import load_dotenv

from core.lead_engine import LeadSuperloopEngine
from models.runtime_factory import RuntimeModelFactory
from superloop_version import __version__, RELEASE_CHANNEL


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Superloop Mission Control")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__} ({RELEASE_CHANNEL})")
    parser.add_argument("--dashboard", action="store_true", help="Launch the local dashboard.")
    parser.add_argument("--host", default="0.0.0.0", help="Dashboard host.")
    parser.add_argument("--port", type=int, default=8000, help="Dashboard port.")

    parser.add_argument("--goal", help="What the manager should build or test.", default=os.getenv("SUPERLOOP_GOAL", ""))
    parser.add_argument("--workspace", help="Workspace directory.", default=os.getenv("SUPERLOOP_WORKSPACE", "."))
    parser.add_argument("--data", help="Optional path to an evaluation data file or benchmark input.", default=os.getenv("SUPERLOOP_DATA_PATH", ""))
    parser.add_argument("--judge_brief", help="How the judge should score success.", default=os.getenv("SUPERLOOP_JUDGE_BRIEF", ""))
    parser.add_argument("--operator_brief", help="Extra direction for the manager before round zero.", default=os.getenv("SUPERLOOP_OPERATOR_BRIEF", ""))
    parser.add_argument("--target_score", type=float, default=_env_float("SUPERLOOP_TARGET_SCORE", 0.8), help="Goal score.")
    parser.add_argument("--max_iters", type=int, default=_env_int("SUPERLOOP_MAX_ITERS", 5), help="Maximum optimization rounds.")
    parser.add_argument("--resume", action="store_true", default=_env_bool("SUPERLOOP_RESUME", False), help="Resume from the latest run in the workspace.")
    parser.add_argument("--manager", help="Manager descriptor.", default=os.getenv("SUPERLOOP_MANAGER", "gemini:gemini-1.5-pro"))
    parser.add_argument(
        "--specialists",
        help="Comma-separated specialist descriptors.",
        default=os.getenv("SUPERLOOP_SPECIALISTS", "openai:gpt-4o,anthropic:claude-3-5-sonnet-20240620"),
    )
    return parser


async def main_async(args: argparse.Namespace | None = None):
    parsed = args
    if parsed is None:
        load_dotenv()
        parser = build_parser()
        parsed = parser.parse_args()

    goal = parsed.goal or input("What should the manager build or test? ").strip()
    data_path = parsed.data or input("Path to the evaluation data file (optional): ").strip()
    judge_brief = parsed.judge_brief or input("Judge brief (optional): ").strip()
    operator_brief = parsed.operator_brief or input("Operator brief (optional): ").strip()

    manager = RuntimeModelFactory.create_model(parsed.manager)
    specialists = [RuntimeModelFactory.create_model(item.strip()) for item in parsed.specialists.split(",") if item.strip()]
    engine = LeadSuperloopEngine(manager=manager, specialists=specialists, workspace_dir=parsed.workspace)

    result = await engine.run(
        goal=goal,
        data_path=data_path,
        max_iterations=parsed.max_iters,
        target_score=parsed.target_score,
        judge_brief=judge_brief,
        operator_brief=operator_brief,
        resume=parsed.resume,
    )

    print(f"\nRun ID: {result['run_id']}")
    print(f"Status: {result['status']}")
    print(f"Score: {result['score']}")
    print(f"Run folder: {result['run_dir']}")


def launch_dashboard(args: argparse.Namespace):
    import uvicorn

    from api.server import app, configure_runtime_defaults

    configure_runtime_defaults(
        goal=args.goal or "",
        workspace_path=args.workspace,
        data_path=args.data or "",
        judge_brief=args.judge_brief or "",
        operator_brief=args.operator_brief or "",
        target_score=args.target_score,
        max_iterations=args.max_iters,
        resume=args.resume,
        manager_descriptor=args.manager,
        specialist_descriptors=[item.strip() for item in args.specialists.split(",") if item.strip()],
    )
    uvicorn.run(app, host=args.host, port=args.port, reload=False)


def cli():
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    if args.dashboard:
        launch_dashboard(args)
        return
    asyncio.run(main_async(args))


if __name__ == "__main__":
    cli()
