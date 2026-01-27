import sys
from pathlib import Path
from time import perf_counter

from lib.script_context import ScriptCtx
import datetime as _dt
from lib.log import setup_logger, get_console
from lib.utils import require_env


def _env_bool(name, default=False):
    val = require_env(name, str(default))
    return default if val is None else val.lower() in {"1", "true", "yes", "on"}


def init_ctx(required_env) -> ScriptCtx:
    # Ensure required env vars exist (and are non-empty)
    for var in required_env:
        require_env(var)
    default_workspace = Path(sys.argv[0]).parent / ".workspace"
    workspace = Path(require_env("WORKSPACE", str(default_workspace))).resolve()
    repo_dir = Path(require_env("REPO_DIR")).resolve()
    script_name = Path(sys.argv[0]).stem
    component = repo_dir.name
    verbose = _env_bool("VERBOSE")

    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir = workspace / ".protoctl-logs" / component
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{script_name}-{ts}.log"

    logger = setup_logger(log_file, verbose)

    ctx = ScriptCtx(
        workspace=workspace,
        repo_dir=repo_dir,
        script_name=script_name,
        component=component,
        verbose=verbose,
        log_file=log_file,
        logger=logger,
    )
    return ctx


def run_script(script, *, required_env=()):
    _console = get_console()
    start = perf_counter()
    try:
        ctx = init_ctx(required_env=required_env)

        # Pretty header
        _console.rule(f"🚀 Running {ctx.script_name}")
        _console.print(
            f"[dim]Workspace :[/] {ctx.workspace}\n[dim]Component :[/] {ctx.component}"
        )

        if _env_bool("DRY_RUN"):
            _console.print("[yellow]⚠ DRY RUN enabled - no changes will be made[/]")
            _console.rule()
            return

        if ctx.log_file:
            _console.print(f"[dim]Log file  :[/] {ctx.log_file}")
        _console.rule()

        script(ctx)

    except KeyboardInterrupt:
        _console.print("[red]⚡ Interrupted by user (Ctrl+C)[/]")
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as e:
        _console.print(f"[red]✘ Script error: {e!r}[/]")
        _console.print_exception()
        sys.exit(1)
    else:
        total = perf_counter() - start
        _console.rule()

        if ctx is not None:
            ok = ctx.sections_ok
            failed = ctx.sections_failed
            total_sections = ctx.sections_total

            if failed:
                _console.print(
                    f"[red]⚠ {failed} section(s) failed out of {total_sections}[/]"
                )
            else:
                _console.print("[green]✅ All sections completed successfully[/]")

            _console.print(f"   Sections : {ok} OK, {failed} failed")
            _console.print(f"   Duration : {total:.1f}s total")
            if ctx.log_file:
                _console.print(f"   Logs     : {ctx.log_file}")
        else:
            _console.print("[red]⚠ No context initialized[/]")

        _console.rule()
