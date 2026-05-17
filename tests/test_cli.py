import io
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from midas_cn.interfaces.cli import main


class CliTest(unittest.TestCase):
    def write_config(self, temp_dir: str) -> str:
        config_path = Path(temp_dir) / "system.toml"
        config_path.write_text(
            f"""
[system]
archive_dir = "{temp_dir}/output/decisions"
report_archive_dir = "{temp_dir}/output/reports"

[universe]
default_symbols = ["600519.SH"]
benchmark_symbols = ["000300.SH"]

[data]
provider = "mock"

[news]
lookback_days = 2
max_items_per_symbol = 20

[cache]
data_dir = "{temp_dir}/output/cache"
ttl_seconds = 86400

[pools]
enabled = false

[llm]
enabled = false

[xueqiu]
enabled = false
""",
            encoding="utf-8",
        )
        return str(config_path)

    def test_cache_status_command_outputs_cache_table(self):
        stream = io.StringIO()
        with redirect_stdout(stream):
            exit_code = main(["cache", "status", "--config", "config/system.toml"])

        output = stream.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("cache_ttl_seconds:", output)
        self.assertIn("| K线 |", output)
        self.assertIn("| 选股池 |", output)

    def test_cache_clear_command_removes_cache_target(self):
        with TemporaryDirectory() as temp_dir:
            config_path = self.write_config(temp_dir)
            cache_file = Path(temp_dir) / "output" / "cache" / "kline" / "sample.json"
            cache_file.parent.mkdir(parents=True)
            cache_file.write_text("{}", encoding="utf-8")
            stream = io.StringIO()

            with redirect_stdout(stream):
                exit_code = main(["cache", "clear", "--config", config_path, "--target", "K线"])

            self.assertEqual(exit_code, 0)
            self.assertIn("removed:", stream.getvalue())
            self.assertFalse(cache_file.exists())

    def test_sources_check_command_uses_mock_config(self):
        with TemporaryDirectory() as temp_dir:
            config_path = self.write_config(temp_dir)
            stream = io.StringIO()

            with redirect_stdout(stream):
                exit_code = main(["sources", "check", "--config", config_path, "--symbol", "600519.SH"])

            self.assertEqual(exit_code, 0)
            self.assertIn("| 市场快照 | success |", stream.getvalue())
            self.assertIn("| 个股上下文 | success |", stream.getvalue())

    def test_report_command_accepts_trade_date(self):
        with TemporaryDirectory() as temp_dir:
            config_path = self.write_config(temp_dir)
            stream = io.StringIO()

            with redirect_stdout(stream):
                exit_code = main([
                    "report",
                    "--config",
                    config_path,
                    "--symbols",
                    "600519.SH",
                    "--no-archive",
                    "--quiet",
                    "--trade-date",
                    "2026-05-08",
                ])

            self.assertEqual(exit_code, 0)
            self.assertIn("run_id: 20260508_155500", stream.getvalue())

    def test_report_refresh_clears_selected_cache_before_run(self):
        with TemporaryDirectory() as temp_dir:
            config_path = self.write_config(temp_dir)
            xueqiu_cache = Path(temp_dir) / "output" / "social" / "xueqiu" / "20260508.json"
            news_cache = Path(temp_dir) / "output" / "cache" / "market_news" / "sample.json"
            xueqiu_cache.parent.mkdir(parents=True)
            news_cache.parent.mkdir(parents=True)
            xueqiu_cache.write_text("{}", encoding="utf-8")
            news_cache.write_text("{}", encoding="utf-8")
            stream = io.StringIO()

            with redirect_stdout(stream):
                exit_code = main([
                    "report",
                    "--config",
                    config_path,
                    "--symbols",
                    "600519.SH",
                    "--no-archive",
                    "--quiet",
                    "--trade-date",
                    "2026-05-08",
                    "--refresh",
                    "xueqiu,market_news",
                ])

            output = stream.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("refresh_targets:", output)
            self.assertFalse(xueqiu_cache.exists())
            self.assertFalse(news_cache.exists())

    def test_config_loads_env_file_next_to_config_without_overriding_process_env(self):
        from midas_cn.config import load_config

        with TemporaryDirectory() as temp_dir:
            config_path = self.write_config(temp_dir)
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                '\n'.join([
                    'XQ_A_TOKEN="from-dotenv"',
                    "XUEQIU_COOKIE=xq_a_token=from-cookie; other=1",
                ]),
                encoding="utf-8",
            )
            old_token = os.environ.pop("XQ_A_TOKEN", None)
            old_cookie = os.environ.get("XUEQIU_COOKIE")
            os.environ["XUEQIU_COOKIE"] = "already-set"
            try:
                load_config(config_path)
                self.assertEqual(os.environ.get("XQ_A_TOKEN"), "from-dotenv")
                self.assertEqual(os.environ.get("XUEQIU_COOKIE"), "already-set")
            finally:
                os.environ.pop("XQ_A_TOKEN", None)
                if old_token is not None:
                    os.environ["XQ_A_TOKEN"] = old_token
                if old_cookie is None:
                    os.environ.pop("XUEQIU_COOKIE", None)
                else:
                    os.environ["XUEQIU_COOKIE"] = old_cookie

    def test_config_loads_simple_exports_from_bashrc(self):
        import midas_cn.config as config_module

        with TemporaryDirectory() as temp_dir:
            config_path = self.write_config(temp_dir)
            bashrc = Path(temp_dir) / ".bashrc"
            bashrc.write_text('export MONGODB_URI="mongodb://from-bashrc"\n', encoding="utf-8")
            old_uri = os.environ.pop("MONGODB_URI", None)
            original_home = config_module.Path.home
            config_module.Path.home = classmethod(lambda cls: Path(temp_dir))
            try:
                config_module.load_config(config_path)
                self.assertEqual(os.environ.get("MONGODB_URI"), "mongodb://from-bashrc")
            finally:
                config_module.Path.home = original_home
                os.environ.pop("MONGODB_URI", None)
                if old_uri is not None:
                    os.environ["MONGODB_URI"] = old_uri


if __name__ == "__main__":
    unittest.main()
