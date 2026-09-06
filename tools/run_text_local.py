"""Start the existing text panel with a loopback B03 form; no remote services."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from betguard.webui import app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--data-dir", type=Path,
                        default=Path(tempfile.gettempdir()) / "betguard-text-local")
    args = parser.parse_args()
    os.environ["BETGUARD_LOCAL_FILL_URL"] = f"http://127.0.0.1:{args.port}"
    os.environ["BETGUARD_SKIP_LICENSE"] = "1"  # isolated test form only
    app.RUNS_DIR = args.data_dir.resolve()
    factory = app.build_workbench_handler

    def local_factory(**kwargs):
        base = factory(**kwargs)

        class LocalHandler(base):
            def do_POST(self):
                if urlparse(self.path).path not in {
                    "/assist-panel/create-batch", "/manual-reparse",
                    "/assist-fill/start", "/assist-fill/mark-done",
                    "/assist-fill/manual-done", "/assist-fill/open-site",
                }:
                    self._send_json({"ok": False, "error": "本機文字驗收模式不呼叫模型或真實網站。"}, status=403)
                    return
                super().do_POST()
        return LocalHandler

    app.build_workbench_handler = local_factory
    print("Local text acceptance only; external model and real-site routes disabled.", flush=True)
    return app.main(["--host", "127.0.0.1", "--port", str(args.port)])


if __name__ == "__main__":
    raise SystemExit(main())
