import io
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


if __name__ == "__main__":
    unittest.main()
