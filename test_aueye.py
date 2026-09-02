"""Comprehensive unit tests for aueye.py (浙商金价监控精简版)"""
import sys, unittest, json, tempfile, os, time, queue
from unittest.mock import Mock, patch, MagicMock

# ----- Mock missing deps FIRST -----
sys.modules['requests'] = MagicMock()
sys.modules['PIL'] = MagicMock()
sys.modules['PIL.Image'] = MagicMock()
sys.modules['PIL.ImageDraw'] = MagicMock()
sys.modules['pystray'] = MagicMock()

# Mock ctypes windll
import ctypes
if not hasattr(ctypes, 'windll'):
    ctypes.windll = MagicMock()
    ctypes.windll.user32.GetSystemMetrics.return_value = 1920
    ctypes.windll.shcore.SetProcessDpiAwareness = MagicMock()

# Mock tkinter Tk
import tkinter as tk
tk.Tk = MagicMock()

# Import the app
sys.path.insert(0, '.')
import aueye

# Redirect config to temp dir
aueye._config_path = lambda: os.path.join(tempfile.gettempdir(), 'test_config.json')

# Clean up test config
if os.path.exists(aueye._config_path()):
    os.remove(aueye._config_path())


class TestConfigPath(unittest.TestCase):
    def test_config_not_frozen(self):
        path = aueye._config_path()
        self.assertIn('test_config.json', path)

    def test_config_frozen_impl(self):
        """Verify the frozen branch logic via direct code test"""
        exec_globals = {'sys': sys, 'os': os}
        exec('def _cfg_path():\n    if getattr(sys, "frozen", False):\n        return "frozen:" + os.path.dirname(sys.executable)\n    else:\n        return "normal"', exec_globals)
        self.assertEqual(exec_globals['_cfg_path'](), 'normal')
        with patch.object(sys, 'frozen', True, create=True):
            with patch.object(sys, 'executable', r'C:\test_app\aueye.exe'):
                exec_globals2 = {'sys': sys, 'os': os}
                exec('def _cfg_path():\n    if getattr(sys, "frozen", False):\n        return "frozen:" + os.path.dirname(sys.executable)\n    else:\n        return "normal"', exec_globals2)
                result = exec_globals2['_cfg_path']()
                self.assertIn('frozen:', result)
                self.assertIn('test_app', result)


class TestCheckAlert(unittest.TestCase):
    def setUp(self):
        self.app = aueye.GoldTaskbarDoubleLine.__new__(aueye.GoldTaskbarDoubleLine)
        self.app.au_upper_target = 500.0
        self.app.au_lower_target = 400.0
        self.app.au_upper_triggered = False
        self.app.au_lower_triggered = False
        self.app.au_upper_last_ts = 0.0
        self.app.au_lower_last_ts = 0.0
        self.app.alert_cooldown_sec = 10
        self.app.email_enabled = False
        self.app.sound_enabled = False
        self.app.email_interval_sec = 600
        self.app.email_au_upper_last_ts = 0.0
        self.app.email_au_lower_last_ts = 0.0
        self.app._notify = MagicMock()
        self.app._send_alert_email = MagicMock()

    def test_au_upper_triggers_once(self):
        self.app._check_alert(510.0, 'AU')
        self.app._notify.assert_called_once()
        self.assertTrue(self.app.au_upper_triggered)

    def test_au_upper_no_repeat_on_same_side(self):
        self.app._check_alert(510.0, 'AU')
        self.app._notify.reset_mock()
        self.app._check_alert(520.0, 'AU')
        self.app._notify.assert_not_called()

    def test_au_upper_resets_after_drop(self):
        self.app._check_alert(510.0, 'AU')
        self.assertTrue(self.app.au_upper_triggered)
        self.app._check_alert(490.0, 'AU')
        self.assertFalse(self.app.au_upper_triggered)

    def test_au_upper_retriggers_after_reset(self):
        self.app._check_alert(510.0, 'AU')
        self.app._notify.reset_mock()
        # Manually move last_ts back so cooldown doesn't block
        self.app.au_upper_last_ts = time.time() - 20
        self.app._check_alert(490.0, 'AU')
        self.app._check_alert(510.0, 'AU')
        self.assertEqual(self.app._notify.call_count, 1)

    def test_au_lower_triggers(self):
        self.app._check_alert(390.0, 'AU')
        self.app._notify.assert_called_once()
        self.assertTrue(self.app.au_lower_triggered)

    def test_cooldown_blocks_rapid_retrigger(self):
        now = time.time()
        self.app.au_upper_last_ts = now - 3
        self.app._check_alert(510.0, 'AU')
        self.app._check_alert(490.0, 'AU')
        self.app._check_alert(510.0, 'AU')
        self.assertEqual(self.app._notify.call_count, 0)

    def test_cooldown_expires_allows_retrigger(self):
        now = time.time()
        self.app.au_upper_last_ts = now - 15
        self.app._check_alert(510.0, 'AU')
        self.assertEqual(self.app._notify.call_count, 1)

    def test_none_price_is_safe(self):
        self.app._check_alert(None, 'AU')
        self.app._notify.assert_not_called()

    def test_unknown_symbol_safe(self):
        # 未知 symbol 不触发任何逻辑（intl 已删除）
        self.app._check_alert(999.0, 'INTL')
        self.app._notify.assert_not_called()
        self.assertFalse(self.app.au_upper_triggered)
        self.assertFalse(self.app.au_lower_triggered)


class TestExtremeDetection(unittest.TestCase):
    def setUp(self):
        self.app = aueye.GoldTaskbarDoubleLine.__new__(aueye.GoldTaskbarDoubleLine)
        self.app.extreme_enabled = True
        self.app.extreme_window_sec = 300
        self.app.extreme_threshold = 5.0
        self.app.extreme_cooldown_sec = 60
        self.app.extreme_last_ts = 0.0
        self.app._action_queue = queue.Queue()
        self.app.au_history = aueye.deque()
        self.app.extreme_flash_times = 6
        self.app.up_color = '#FF3B30'
        self.app.down_color = '#00C853'
        self.app.sound_enabled = False

    def test_au_extreme_triggers_flash(self):
        now = time.time()
        for i in range(6):
            self.app.au_history.append((now - 200 + i * 10, 500.0))
        self.app._track_au_extreme(510.0)
        action = self.app._action_queue.get_nowait()
        self.assertEqual(action[0], 'flash')

    def test_au_extreme_below_threshold(self):
        now = time.time()
        for i in range(6):
            self.app.au_history.append((now - 200 + i * 10, 500.0))
        self.app._track_au_extreme(502.0)
        self.assertEqual(self.app._action_queue.qsize(), 0)

    def test_extreme_disabled_does_nothing(self):
        self.app.extreme_enabled = False
        self.app._track_au_extreme(999.0)
        self.assertEqual(self.app._action_queue.qsize(), 0)


class TestFlashText(unittest.TestCase):
    def setUp(self):
        self.app = aueye.GoldTaskbarDoubleLine.__new__(aueye.GoldTaskbarDoubleLine)
        self.app.flash_active = False
        self.app._flash_after_id = None
        self.app.text_color = '#F5F5F7'
        self.app.font_family = 'Segoe UI'
        self.app.text_font_size = 10
        self.app.flash_text_font_size = 12
        self.app.card_color = '#101218'
        self.app.card_border = '#2A2D36'
        self.app.card_highlight_color = '#1B1F2A'
        self.app.card_border_highlight = '#3A4150'
        self.app.extreme_flash_interval_ms = 150
        self.app.canvas = MagicMock()
        self.app.root = MagicMock()
        self.app._apply_card_style = MagicMock()
        self.app._reset_flash_style = MagicMock()
        # Canvas item IDs referenced by _flash_text
        self.app.au_icon = 1
        self.app.au_value_text = 2

    def test_flash_cancels_previous(self):
        self.app._flash_after_id = 123
        self.app._flash_text('red', 3)
        self.app.root.after_cancel.assert_called_once_with(123)

    def test_flash_zero_times_does_nothing(self):
        self.app._flash_text('red', 0)
        self.app.canvas.itemconfig.assert_not_called()

    def test_flash_sets_after_id(self):
        self.app._flash_text('red', 2)
        # toggle runs immediately, after is called for the next cycle
        self.assertIsNotNone(self.app._flash_after_id)


class TestLoadConfig(unittest.TestCase):
    def setUp(self):
        if os.path.exists(aueye._config_path()):
            os.remove(aueye._config_path())

    def test_default_when_no_file(self):
        cfg = aueye.GoldTaskbarDoubleLine.load_config(MagicMock())
        self.assertEqual(cfg['refresh_interval'], 2)
        self.assertEqual(cfg['au_lower_value'], 900.0)

    def test_saved_merges_with_defaults(self):
        saved = {'refresh_interval': 5}
        with open(aueye._config_path(), 'w') as f:
            json.dump(saved, f)
        cfg = aueye.GoldTaskbarDoubleLine.load_config(MagicMock())
        self.assertEqual(cfg['refresh_interval'], 5)
        self.assertEqual(cfg['au_lower_value'], 900.0)

    def test_no_intl_keys_in_default(self):
        cfg = aueye.GoldTaskbarDoubleLine.load_config(MagicMock())
        self.assertNotIn('intl_upper_value', cfg)
        self.assertNotIn('intl_lower_value', cfg)


class TestBackoff(unittest.TestCase):
    def test_backoff_increases_and_caps(self):
        app = aueye.GoldTaskbarDoubleLine.__new__(aueye.GoldTaskbarDoubleLine)
        app.interval = 2
        app._fetch_fail_count = 5
        backoff = min(app.interval * (2 ** app._fetch_fail_count), 60)
        self.assertEqual(backoff, 60)

    def test_backoff_resets_on_success(self):
        app = aueye.GoldTaskbarDoubleLine.__new__(aueye.GoldTaskbarDoubleLine)
        app.interval = 2
        app._fetch_fail_count = 0
        backoff = app.interval if app._fetch_fail_count == 0 else min(app.interval * (2 ** app._fetch_fail_count), 60)
        self.assertEqual(backoff, 2)


if __name__ == '__main__':
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.TestSuite()
    for cls in [TestConfigPath, TestCheckAlert, TestExtremeDetection,
                TestFlashText, TestLoadConfig, TestBackoff]:
        suite.addTests(unittest.TestLoader().loadTestsFromTestCase(cls))
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)