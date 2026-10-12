"""Comprehensive unit tests for aueye.py (浙商金价监控精简版)"""
import sys, unittest, json, tempfile, os, time, queue
from unittest.mock import Mock, patch, MagicMock

# ----- 依赖可用性：优先使用真实库（高保真 GUI 测试），仅在缺失时降级为 Mock -----
import importlib
import ctypes
import tkinter as tk


def _ensure_import(name):
    """导入第三方依赖；仅在真实库缺失时注入 MagicMock，保证测试模块始终可加载。"""
    try:
        return importlib.import_module(name)
    except Exception:
        sys.modules[name] = MagicMock()
        return sys.modules[name]


for _dep in ('requests', 'PIL', 'PIL.Image', 'PIL.ImageDraw', 'PIL.ImageFont', 'pystray'):
    _ensure_import(_dep)

# ctypes.windll 仅 Windows 存在
if not hasattr(ctypes, 'windll'):
    ctypes.windll = MagicMock()
    ctypes.windll.user32.GetSystemMetrics.return_value = 1920
    ctypes.windll.shcore.SetProcessDpiAwareness = MagicMock()

# Import the app
sys.path.insert(0, '.')
import aueye

# 先保存真实实现（下面会把 _config_path 换成临时路径）
_real_config_path = aueye._config_path
_real_log_file = aueye._log_file

# Redirect config to temp dir
aueye._config_path = lambda: os.path.join(tempfile.gettempdir(), 'test_config.json')

# Clean up test config
if os.path.exists(aueye._config_path()):
    os.remove(aueye._config_path())


class TestConfigPath(unittest.TestCase):
    """路径解析：直接测真实实现（此前只测了一份复制出来的逻辑）。"""

    def test_config_not_frozen(self):
        path = aueye._config_path()
        self.assertIn('test_config.json', path)

    def test_real_config_path_uses_source_dir_when_not_frozen(self):
        path = _real_config_path()
        self.assertTrue(path.endswith('config.json'))
        self.assertEqual(os.path.dirname(path),
                         os.path.dirname(os.path.abspath(aueye.__file__)))

    def test_real_config_path_uses_exe_dir_when_frozen(self):
        with patch.object(sys, 'frozen', True, create=True):
            with patch.object(sys, 'executable', r'C:\frozen_app\aueye.exe'):
                path = _real_config_path()
        self.assertEqual(path, os.path.join(r'C:\frozen_app', 'config.json'))

    def test_real_log_file_uses_source_dir_when_not_frozen(self):
        path = _real_log_file()
        self.assertTrue(path.endswith('aueye.log'))
        self.assertEqual(os.path.dirname(path),
                         os.path.dirname(os.path.abspath(aueye.__file__)))

    def test_real_log_file_uses_exe_dir_when_frozen(self):
        with patch.object(sys, 'frozen', True, create=True):
            with patch.object(sys, 'executable', r'C:\frozen_app\aueye.exe'):
                path = _real_log_file()
        self.assertEqual(path, os.path.join(r'C:\frozen_app', 'aueye.log'))


def _file_handlers():
    return [h for h in aueye.log.handlers if isinstance(h, aueye.logging.FileHandler)]


class TestLogFileHandlers(unittest.TestCase):
    """日志文件开关：attach/detach 的幂等性、异常吞掉行为。"""

    def setUp(self):
        self.tmp_log = os.path.join(tempfile.gettempdir(), 'aueye_test_run.log')
        if os.path.exists(self.tmp_log):
            os.remove(self.tmp_log)
        self._p = patch.object(aueye, '_log_file', lambda: self.tmp_log)
        self._p.start()

    def tearDown(self):
        aueye._detach_log_file()
        for h in _file_handlers():
            try:
                h.stream.close()
            except Exception:
                pass
        self._p.stop()
        if os.path.exists(self.tmp_log):
            os.remove(self.tmp_log)

    def test_attach_adds_single_file_handler(self):
        aueye._attach_log_file()
        aueye._attach_log_file()  # second call should return early
        self.assertEqual(len(_file_handlers()), 1)
        self.assertTrue(os.path.exists(self.tmp_log))

    def test_detach_removes_file_handler(self):
        aueye._attach_log_file()
        self.assertEqual(len(_file_handlers()), 1)
        aueye._detach_log_file()
        self.assertEqual(len(_file_handlers()), 0)
        aueye._detach_log_file()  # safe when nothing left

    def test_attach_swallows_file_handler_errors(self):
        # point at a directory: FileHandler open fails and must be swallowed
        with patch.object(aueye, '_log_file', lambda: tempfile.gettempdir()):
            aueye._attach_log_file()
        self.assertEqual(len(_file_handlers()), 0)

    def test_detach_swallows_close_errors(self):
        aueye._attach_log_file()
        handler = _file_handlers()[0]
        with patch.object(handler, 'close', side_effect=OSError('busy')):
            aueye._detach_log_file()
        self.assertEqual(len(_file_handlers()), 0)
        handler.stream.close()  # release handle or Windows cannot delete

    def test_logger_writes_to_file_when_enabled(self):
        aueye._attach_log_file()
        aueye.log.info("hello-from-test")
        for h in _file_handlers():
            h.flush()
        with open(self.tmp_log, encoding='utf-8') as f:
            self.assertIn("hello-from-test", f.read())


class TestDpiAwareness(unittest.TestCase):
    """DPI 适配：shcore 失败回退 user32，两者都失败则静默。"""

    def test_shcore_success_does_not_touch_user32(self):
        shcore = MagicMock()
        user32 = MagicMock()
        with patch.object(ctypes.windll, 'shcore', shcore, create=True), \
             patch.object(ctypes.windll, 'user32', user32, create=True):
            aueye._enable_dpi_awareness()
        shcore.SetProcessDpiAwareness.assert_called_once_with(1)
        user32.SetProcessDPIAware.assert_not_called()

    def test_shcore_failure_falls_back_to_user32(self):
        shcore = MagicMock()
        shcore.SetProcessDpiAwareness.side_effect = OSError('not supported')
        user32 = MagicMock()
        with patch.object(ctypes.windll, 'shcore', shcore, create=True), \
             patch.object(ctypes.windll, 'user32', user32, create=True):
            aueye._enable_dpi_awareness()
        user32.SetProcessDPIAware.assert_called_once_with()

    def test_total_failure_is_silent(self):
        shcore = MagicMock()
        shcore.SetProcessDpiAwareness.side_effect = OSError('x')
        user32 = MagicMock()
        user32.SetProcessDPIAware.side_effect = OSError('y')
        with patch.object(ctypes.windll, 'shcore', shcore, create=True), \
             patch.object(ctypes.windll, 'user32', user32, create=True):
            aueye._enable_dpi_awareness()  # 不应抛异常


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


class TestTimesharing(unittest.TestCase):
    def setUp(self):
        self.app = aueye.GoldTaskbarDoubleLine.__new__(aueye.GoldTaskbarDoubleLine)
        self.app._timesharing_data = []
        self.app._timesharing_pre_close = None
        self.app._sparkline_item_ids = []
        self.app._sparkline_visible = False
        self.app.session = MagicMock()
        self.app.muted_color = '#9AA0A6'
        self.app.up_color = '#FF3B30'
        self.app.down_color = '#00C853'
        self.app.font_family = 'Segoe UI'
        self.app.card_width = 220
        self.app.card_height = 130
        self.app.card_color = '#101218'
        self.app.card_border = '#2A2D36'
        self.app.corner_radius = 15
        self.app.canvas = MagicMock()
        self.app._price_lock = __import__('threading').Lock()

    def test_fetch_timesharing_success(self):
        """分时API返回正常数据时正确解析。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "success": True,
            "resultData": {
                "code": "00000000",
                "data": {
                    "preClose": 930.0,
                    "timeSharingDotItemDTOList": [
                        {"lastPrice": 931.5, "tradeDateTime": "10:01"},
                        {"lastPrice": 932.0, "tradeDateTime": "10:02"},
                    ]
                }
            }
        }
        mock_resp.raise_for_status = MagicMock()
        self.app.session.post.return_value = mock_resp

        result = self.app._fetch_timesharing()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["price"], 931.5)
        self.assertEqual(self.app._timesharing_pre_close, 930.0)

    def test_fetch_timesharing_empty(self):
        """分时API返回空数据时不崩溃。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": True, "resultData": {"code": "00000000", "data": {"preClose": 930.0, "timeSharingDotItemDTOList": []}}}
        mock_resp.raise_for_status = MagicMock()
        self.app.session.post.return_value = mock_resp

        result = self.app._fetch_timesharing()
        self.assertEqual(len(result), 0)

    def test_fetch_timesharing_api_failure(self):
        """分时API失败时不影响程序。"""
        self.app.session.post.side_effect = Exception("network error")
        result = self.app._fetch_timesharing()
        self.assertEqual(result, [])

    def test_sparkline_no_data(self):
        """无分时数据时折线图不显示。"""
        self.app._timesharing_data = []
        self.app._draw_sparkline()
        self.assertFalse(self.app._sparkline_visible)

    def test_sparkline_with_data(self):
        """有分时数据时正确生成折线坐标。"""
        self.app._timesharing_data = [
            {"price": 930.0, "time": "10:01"},
            {"price": 932.0, "time": "10:02"},
            {"price": 931.0, "time": "10:03"},
        ]
        self.app._timesharing_pre_close = 930.5
        self.app.canvas.create_line = MagicMock(return_value=1)
        self.app.canvas.create_text = MagicMock(return_value=2)

        self.app._draw_sparkline()
        self.assertTrue(self.app._sparkline_visible)
        self.assertGreater(self.app.canvas.create_line.call_count, 0)

    def test_sparkline_flat_price(self):
        """价格不变时纵轴仍有合理范围。"""
        self.app._timesharing_data = [
            {"price": 930.0, "time": "10:01"},
            {"price": 930.0, "time": "10:02"},
            {"price": 930.0, "time": "10:03"},
        ]
        self.app._timesharing_pre_close = None
        self.app.canvas.create_line = MagicMock(return_value=1)
        self.app.canvas.create_text = MagicMock(return_value=2)

        self.app._draw_sparkline()
        self.assertTrue(self.app._sparkline_visible)


class TestFontCache(unittest.TestCase):
    """字体缓存：_find_font 按 (size, bold) 复用实例。"""

    def tearDown(self):
        aueye._FONT_CACHE.clear()

    def test_font_cached_by_key(self):
        """同参数两次调用返回同一对象。"""
        aueye._FONT_CACHE.clear()
        f1 = aueye._find_font(size=22, bold=True)
        f2 = aueye._find_font(size=22, bold=True)
        self.assertIs(f1, f2)

    def test_cache_separates_sizes_and_bold(self):
        """不同 size / bold 走不同缓存项。"""
        aueye._FONT_CACHE.clear()
        aueye._find_font(size=22, bold=True)
        aueye._find_font(size=22, bold=False)
        aueye._find_font(size=40, bold=True)
        self.assertEqual(len(aueye._FONT_CACHE), 3)

    def test_cache_survives_missing_fonts(self):
        """字体全都找不到时回退默认字体且仍被缓存。"""
        aueye._FONT_CACHE.clear()
        with patch('aueye.os.path.exists', return_value=False):
            f1 = aueye._find_font(size=18, bold=False)
            f2 = aueye._find_font(size=18, bold=False)
        self.assertIs(f1, f2)
        self.assertIn((18, False), aueye._FONT_CACHE)


class TestTimesharingThrottle(unittest.TestCase):
    """分时刷新节流：应按 60 秒间隔触发，而非数据刷新间隔。"""

    def test_refreshes_about_every_60s(self):
        # interval=2 秒 → 每 30 次（60s）刷新一次
        self.assertFalse(aueye.timesharing_needed(29, 2))
        self.assertTrue(aueye.timesharing_needed(30, 2))

    def test_never_more_frequent_than_one_interval(self):
        """interval 很大时至少 1 次计数就刷新。"""
        self.assertTrue(aueye.timesharing_needed(1, 120))

    def test_invalid_interval_falls_back(self):
        """非法 interval 不抛异常，默认按 30 次计数。"""
        self.assertFalse(aueye.timesharing_needed(29, None))
        self.assertTrue(aueye.timesharing_needed(30, "abc"))


class TestTrayIconSignature(unittest.TestCase):
    """托盘图标：价格未变时不应重复渲染。"""

    def _make_app(self):
        app = aueye.GoldTaskbarDoubleLine.__new__(aueye.GoldTaskbarDoubleLine)
        app.tray = MagicMock()
        app.tray.visible = True
        return app

    @staticmethod
    def _refresh(app, au, prev):
        """复刻 _update_ui_cycle 中的托盘刷新逻辑。"""
        sig = (au, prev)
        if sig != getattr(app, "_tray_sig", None):
            app.tray.icon = app._make_tray_icon(price=au, prev_price=prev)
            app._tray_sig = sig

    def test_no_rerender_when_price_unchanged(self):
        app = self._make_app()
        app._make_tray_icon = MagicMock(return_value='img')
        self._refresh(app, 931.5, 930.0)
        self._refresh(app, 931.5, 930.0)
        self.assertEqual(app._make_tray_icon.call_count, 1)

    def test_rerender_when_price_changes(self):
        app = self._make_app()
        app._make_tray_icon = MagicMock(return_value='img')
        self._refresh(app, 931.5, 930.0)
        self._refresh(app, 932.0, 931.5)
        self.assertEqual(app._make_tray_icon.call_count, 2)


# ===========================================================================
# 以下为覆盖率补测：真实库 + 真实 Tk 控件，仅对网络/线程/消息框打桩
# ===========================================================================

def _bare_app(**attrs):
    """构造一个绕过 __init__ 的 app，注入最小可用属性集。"""
    app = aueye.GoldTaskbarDoubleLine.__new__(aueye.GoldTaskbarDoubleLine)
    app.root = MagicMock()
    app.root.attributes.return_value = "0.55"
    app.config = dict(aueye.DEFAULT_CONFIG)
    app.card_fill_items = []      # __init__ 中初始化（aueye.py:469-470）
    app.card_border_items = []
    app.interval = 2
    app.extreme_window_sec = 300
    app.extreme_threshold = 5.0
    app.extreme_enabled = True
    app.extreme_flash_times = 6
    app.extreme_flash_interval_ms = 150
    app.extreme_cooldown_sec = 60
    app.alert_cooldown_sec = 10
    app.email_interval_sec = 600
    app.au_history = aueye.deque()
    for k, v in attrs.items():
        setattr(app, k, v)
    return app


# ------------------------------------------------------------------ 数据抓取
class TestFetchAu(unittest.TestCase):
    """_fetch_au：京东金融行情接口的成功与各类失败分支。"""

    def setUp(self):
        self.app = aueye.GoldTaskbarDoubleLine.__new__(aueye.GoldTaskbarDoubleLine)
        self.app.session = MagicMock()

    def _respond(self, payload, raise_exc=None):
        resp = MagicMock()
        resp.json.return_value = payload
        if raise_exc is not None:
            resp.raise_for_status.side_effect = raise_exc
        self.app.session.post.return_value = resp
        return resp

    def test_success_returns_float(self):
        self._respond({"success": True,
                       "resultData": {"code": "00000000", "data": {"price": "931.5"}}})
        self.assertEqual(self.app._fetch_au(), 931.5)

    def test_success_flag_false_returns_none(self):
        self._respond({"success": False, "resultData": {"code": "00000000", "data": {"price": 931.5}}})
        self.assertIsNone(self.app._fetch_au())

    def test_business_code_mismatch_returns_none(self):
        self._respond({"success": True, "resultData": {"code": "99999999", "data": {"price": 931.5}}})
        self.assertIsNone(self.app._fetch_au())

    def test_missing_price_field_returns_none(self):
        self._respond({"success": True, "resultData": {"code": "00000000", "data": {}}})
        self.assertIsNone(self.app._fetch_au())

    def test_non_numeric_price_returns_none(self):
        self._respond({"success": True,
                       "resultData": {"code": "00000000", "data": {"price": "not-a-number"}}})
        self.assertIsNone(self.app._fetch_au())

    def test_http_error_returns_none(self):
        self._respond({}, raise_exc=RuntimeError("500 Server Error"))
        self.assertIsNone(self.app._fetch_au())

    def test_timeout_returns_none(self):
        self.app.session.post.side_effect = TimeoutError("timed out")
        self.assertIsNone(self.app._fetch_au())

    def test_request_uses_expected_endpoint_and_payload(self):
        self._respond({"success": True,
                       "resultData": {"code": "00000000", "data": {"price": 930}}})
        self.app._fetch_au()
        args, kwargs = self.app.session.post.call_args
        self.assertIn("cfGetLatestPriceInfo", args[0])
        self.assertEqual(kwargs["json"], {"reqData": {"productSku": "1961543816"}})
        self.assertEqual(kwargs["timeout"], 2)


class TestFetchTimesharingEdge(unittest.TestCase):
    """_fetch_timesharing：脏数据点与昨收价非法时的容错。"""

    def setUp(self):
        self.app = aueye.GoldTaskbarDoubleLine.__new__(aueye.GoldTaskbarDoubleLine)
        self.app._timesharing_data = []
        self.app._timesharing_pre_close = None
        self.app.session = MagicMock()
        self.app._price_lock = aueye.threading.Lock()

    def _respond(self, data):
        resp = MagicMock()
        resp.json.return_value = {"success": True, "resultData": {"code": "00000000", "data": data}}
        self.app.session.post.return_value = resp

    def test_bad_price_point_skipped(self):
        """点里的价格无法转 float 时跳过该点而非崩溃。"""
        self._respond({"preClose": 930.0, "timeSharingDotItemDTOList": [
            {"lastPrice": "abc", "tradeDateTime": "10:01"},
            {"lastPrice": 931.0, "tradeDateTime": "10:02"},
        ]})
        result = self.app._fetch_timesharing()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["price"], 931.0)

    def test_bad_pre_close_ignored(self):
        """昨收价非法时保留旧值。"""
        self.app._timesharing_pre_close = 555.0
        self._respond({"preClose": "nope", "timeSharingDotItemDTOList": [
            {"lastPrice": 931.0, "tradeDateTime": "10:02"},
        ]})
        self.app._fetch_timesharing()
        self.assertEqual(self.app._timesharing_pre_close, 555.0)

    def test_alternative_field_names(self):
        """兼容 curPrice / tradeTime 备用字段名。"""
        self._respond({"preClose": 930.0, "timeSharingDotDtoList": [
            {"curPrice": 932.5, "tradeTime": "10:03"},
        ]})
        result = self.app._fetch_timesharing()
        self.assertEqual(result, [{"price": 932.5, "time": "10:03"}])

    def test_point_without_time_skipped(self):
        """缺少时间的点被跳过。"""
        self._respond({"preClose": 930.0, "timeSharingDotItemDTOList": [
            {"lastPrice": 931.0},
            {"lastPrice": 932.0, "tradeDateTime": "10:09"},
        ]})
        result = self.app._fetch_timesharing()
        self.assertEqual(len(result), 1)

    def test_business_code_mismatch_returns_empty(self):
        resp = MagicMock()
        resp.json.return_value = {"success": True, "resultData": {"code": "1", "data": {}}}
        self.app.session.post.return_value = resp
        self.assertEqual(self.app._fetch_timesharing(), [])


# ------------------------------------------------------------------ 配置
class TestApplyConfig(unittest.TestCase):
    """apply_config：默认值回退、数值钳制、开关联动。"""

    def test_merges_into_existing_config(self):
        app = _bare_app()
        app.config["keep_me"] = 1
        app.apply_config({"refresh_interval": 5})
        self.assertEqual(app.config["keep_me"], 1)
        self.assertEqual(app.interval, 5.0)

    def test_interval_and_opacity_applied(self):
        app = _bare_app()
        app.apply_config({"refresh_interval": 3, "opacity": 0.8})
        self.assertEqual(app.interval, 3.0)
        app.root.attributes.assert_any_call("-alpha", 0.8)

    def test_target_disabled_returns_none(self):
        app = _bare_app()
        app.apply_config({"au_upper_enabled": False, "au_upper_value": 1000.0,
                          "au_lower_enabled": False, "au_lower_value": 900.0})
        self.assertIsNone(app.au_upper_target)
        self.assertIsNone(app.au_lower_target)

    def test_target_enabled_returns_float(self):
        app = _bare_app()
        app.apply_config({"au_upper_enabled": True, "au_upper_value": "1000.5",
                          "au_lower_enabled": True, "au_lower_value": 900})
        self.assertEqual(app.au_upper_target, 1000.5)
        self.assertEqual(app.au_lower_target, 900.0)

    def test_target_with_bad_value_returns_none(self):
        """启用但数值非法 → target 返回 None（容错而非崩溃）。"""
        app = _bare_app()
        app.apply_config({"au_upper_enabled": True, "au_upper_value": "abc"})
        self.assertIsNone(app.au_upper_target)

    def test_extreme_window_converted_to_seconds(self):
        app = _bare_app()
        app.apply_config({"extreme_window_min": 7})
        self.assertEqual(app.extreme_window_sec, 420)

    def test_extreme_window_min_from_default_config(self):
        """Config missing extreme_window_min falls back to DEFAULT_CONFIG minutes."""
        app = _bare_app()
        app.apply_config({"refresh_interval": 2})
        self.assertEqual(app.extreme_window_sec,
                         int(aueye.DEFAULT_CONFIG["extreme_window_min"]) * 60)

    def test_email_interval_clamped_to_minimum_300(self):
        app = _bare_app()
        app.apply_config({"email_interval_sec": 10})
        self.assertEqual(app.email_interval_sec, 300)

    def test_email_interval_kept_when_large(self):
        app = _bare_app()
        app.apply_config({"email_interval_sec": 900})
        self.assertEqual(app.email_interval_sec, 900)

    def test_smtp_host_blank_falls_back_to_default(self):
        app = _bare_app()
        app.apply_config({"smtp_host": "   "})
        self.assertEqual(app.smtp_host, "smtp.163.com")

    def test_smtp_host_trimmed(self):
        app = _bare_app()
        app.apply_config({"smtp_host": "  smtp.qq.com  "})
        self.assertEqual(app.smtp_host, "smtp.qq.com")

    def test_email_and_sound_flags(self):
        app = _bare_app()
        app.apply_config({"email_enabled": 1, "sound_enabled": 0,
                          "email_sender": "a@b.c", "email_password": "p",
                          "email_receiver": "d@e.f", "smtp_port": "587", "smtp_ssl": 0})
        self.assertTrue(app.email_enabled)
        self.assertFalse(app.sound_enabled)
        self.assertEqual(app.email_sender, "a@b.c")
        self.assertEqual(app.email_receiver, "d@e.f")
        self.assertEqual(app.smtp_port, 587)
        self.assertFalse(app.smtp_ssl)

    def test_history_cleared(self):
        app = _bare_app()
        app.au_history.append((1.0, 900.0))
        app.apply_config({})
        self.assertEqual(len(app.au_history), 0)

    def test_log_disabled_detaches_file_handler(self):
        app = _bare_app()
        with patch.object(aueye, '_detach_log_file') as detach, \
             patch.object(aueye, '_attach_log_file') as attach:
            app.apply_config({"log_enabled": False})
        detach.assert_called_once_with()
        attach.assert_not_called()

    def test_log_enabled_attaches_file_handler(self):
        app = _bare_app()
        tmp_log = os.path.join(tempfile.gettempdir(), 'aueye_applycfg.log')
        if os.path.exists(tmp_log):
            os.remove(tmp_log)
        with patch.object(aueye, '_log_file', lambda: tmp_log), \
             patch.object(aueye, '_detach_log_file') as detach:
            app.apply_config({"log_enabled": True})
        try:
            self.assertTrue(app.log_enabled)
            self.assertTrue(_file_handlers())
            detach.assert_not_called()
        finally:
            aueye._detach_log_file()
            for h in _file_handlers():
                try:
                    h.stream.close()
                except Exception:
                    pass
            if os.path.exists(tmp_log):
                os.remove(tmp_log)


class TestSaveConfigRoundTrip(unittest.TestCase):
    """save_config / load_config 落盘与读回。"""

    def setUp(self):
        self.path = os.path.join(tempfile.gettempdir(), 'aueye_save_config_test.json')
        if os.path.exists(self.path):
            os.remove(self.path)
        self._p = patch.object(aueye, '_config_path', lambda: self.path)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_save_then_load_round_trip(self):
        app = _bare_app()
        app.config = {"refresh_interval": 9, "opacity": 0.7, "email_sender": "中文@测试"}
        app.save_config()
        loaded = app.load_config()
        self.assertEqual(loaded["refresh_interval"], 9)
        self.assertEqual(loaded["email_sender"], "中文@测试")
        # 其余键由 DEFAULT_CONFIG 补齐
        self.assertIn("extreme_threshold", loaded)
        with open(self.path, encoding="utf-8") as f:
            self.assertIn("中文@测试", f.read())   # ensure_ascii=False

    def test_load_missing_file_returns_defaults(self):
        app = _bare_app()
        self.assertEqual(app.load_config(), aueye.DEFAULT_CONFIG)

    def test_load_corrupt_json_returns_defaults(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        app = _bare_app()
        self.assertEqual(app.load_config(), aueye.DEFAULT_CONFIG)

    def test_save_swallows_write_errors(self):
        app = _bare_app()
        with patch.object(aueye, '_config_path', lambda: os.path.join(tempfile.gettempdir(), 'no_such_dir_x', 'c.json')):
            app.save_config()   # 不应抛异常
        self.assertFalse(os.path.exists(self.path))

    def test_save_uses_indent_two(self):
        app = _bare_app()
        app.config = {"refresh_interval": 2}
        app.save_config()
        with open(self.path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn('\n  "refresh_interval"', text)


# ------------------------------------------------------------------ 通知 / 邮件
class TestNotifyAndSound(unittest.TestCase):
    """_notify / _play_sound 的可用性分支。"""

    def test_notify_uses_tray_when_visible(self):
        app = _bare_app()
        app.tray = MagicMock()
        app.tray.visible = True
        with patch.object(aueye.log, 'info') as log_info:
            app._notify("测试消息")
        app.tray.notify.assert_called_once_with("测试消息", "浙商金提醒")
        log_info.assert_not_called()

    def test_notify_falls_back_to_log_when_no_tray(self):
        app = _bare_app(tray=None)
        with patch.object(aueye.log, 'info') as log_info:
            app._notify("测试消息")
        log_info.assert_called_once()

    def test_notify_falls_back_when_tray_raises(self):
        app = _bare_app()
        app.tray = MagicMock()
        app.tray.visible = True
        app.tray.notify.side_effect = RuntimeError("tray dead")
        with patch.object(aueye.log, 'info') as log_info:
            app._notify("测试消息")
        log_info.assert_called_once()

    def test_notify_falls_back_when_tray_hidden(self):
        app = _bare_app()
        app.tray = MagicMock()
        app.tray.visible = False
        with patch.object(aueye.log, 'info') as log_info:
            app._notify("隐藏时")
        app.tray.notify.assert_not_called()
        log_info.assert_called_once()

    def test_play_sound_disabled_does_nothing(self):
        app = _bare_app(sound_enabled=False)
        with patch.object(aueye, 'winsound', MagicMock()), \
             patch.object(aueye.threading, 'Thread') as thread_cls:
            app._play_sound()
        thread_cls.assert_not_called()

    def test_play_sound_no_winsound_module(self):
        app = _bare_app(sound_enabled=True)
        with patch.object(aueye, 'winsound', None), \
             patch.object(aueye.threading, 'Thread') as thread_cls:
            app._play_sound()
        thread_cls.assert_not_called()

    def test_play_sound_starts_beep_thread(self):
        app = _bare_app(sound_enabled=True)
        fake_winsound = MagicMock()
        with patch.object(aueye, 'winsound', fake_winsound), \
             patch.object(aueye.threading, 'Thread') as thread_cls:
            app._play_sound()
        thread_cls.assert_called_once()
        self.assertEqual(thread_cls.call_args.kwargs["args"], (880, 180))
        self.assertTrue(thread_cls.call_args.kwargs["daemon"])
        thread_cls.return_value.start.assert_called_once_with()

    def test_play_sound_swallows_thread_errors(self):
        app = _bare_app(sound_enabled=True)
        with patch.object(aueye, 'winsound', MagicMock()), \
             patch.object(aueye.threading, 'Thread', side_effect=RuntimeError("no thread")):
            app._play_sound()   # 不应抛异常


class TestSendEmail(unittest.TestCase):
    """send_email：SSL / STARTTLS 两条链路与异常返回。"""

    def setUp(self):
        self.app = _bare_app(smtp_host="smtp.163.com", smtp_port=465, smtp_ssl=True)

    def test_ssl_path_success(self):
        server = MagicMock()
        with patch.object(aueye.smtplib, 'SMTP_SSL', return_value=server) as ssl_cls:
            ok, err = self.app.send_email("a@b.c", "pw", "d@e.f", "主题", "正文")
        self.assertEqual((ok, err), (True, ""))
        ssl_cls.assert_called_once_with("smtp.163.com", 465, timeout=10)
        server.starttls.assert_not_called()
        server.login.assert_called_once_with("a@b.c", "pw")
        self.assertEqual(server.sendmail.call_args[0][0], "a@b.c")
        self.assertEqual(server.sendmail.call_args[0][1], ["d@e.f"])

    def test_plain_path_calls_starttls(self):
        self.app.smtp_ssl = False
        self.app.smtp_port = 587
        server = MagicMock()
        with patch.object(aueye.smtplib, 'SMTP', return_value=server) as plain_cls:
            ok, err = self.app.send_email("a@b.c", "pw", "d@e.f", "主题", "正文")
        self.assertEqual((ok, err), (True, ""))
        plain_cls.assert_called_once_with("smtp.163.com", 587, timeout=10)
        server.starttls.assert_called_once_with()

    def test_login_failure_returns_error_message(self):
        server = MagicMock()
        server.login.side_effect = RuntimeError("认证失败")
        with patch.object(aueye.smtplib, 'SMTP_SSL', return_value=server):
            ok, err = self.app.send_email("a@b.c", "pw", "d@e.f", "主题", "正文")
        self.assertFalse(ok)
        self.assertIn("认证失败", err)

    def test_connect_failure_returns_error_message(self):
        with patch.object(aueye.smtplib, 'SMTP_SSL', side_effect=OSError("connection refused")):
            ok, err = self.app.send_email("a@b.c", "pw", "d@e.f", "主题", "正文")
        self.assertFalse(ok)
        self.assertIn("connection refused", err)

    def test_message_headers_filled(self):
        server = MagicMock()
        with patch.object(aueye.smtplib, 'SMTP_SSL', return_value=server):
            self.app.send_email("a@b.c", "pw", "d@e.f", "浙商金监控", "正文内容")
        raw = server.sendmail.call_args[0][2]
        self.assertIn("Subject:", raw)
        self.assertIn("From: a@b.c", raw)
        self.assertIn("To: d@e.f", raw)

    def test_defaults_used_when_attributes_missing(self):
        bare = aueye.GoldTaskbarDoubleLine.__new__(aueye.GoldTaskbarDoubleLine)
        server = MagicMock()
        with patch.object(aueye.smtplib, 'SMTP_SSL', return_value=server) as ssl_cls:
            ok, _ = bare.send_email("a@b.c", "pw", "d@e.f", "s", "b")
        self.assertTrue(ok)
        ssl_cls.assert_called_once_with("smtp.163.com", 465, timeout=10)


class TestSendAlertEmail(unittest.TestCase):
    """_send_alert_email：未启用 / 未配置时不启动线程。"""

    def _app(self, **kw):
        base = dict(email_enabled=True, email_sender="a@b.c",
                    email_password="pw", email_receiver="d@e.f")
        base.update(kw)
        app = _bare_app(**base)
        app.send_email = MagicMock()
        return app

    def test_sends_when_configured(self):
        app = self._app()
        with patch.object(aueye.threading, 'Thread') as thread_cls:
            app._send_alert_email("主题", "正文")
        thread_cls.assert_called_once()
        self.assertTrue(thread_cls.call_args.kwargs["daemon"])
        thread_cls.return_value.start.assert_called_once_with()

    def test_skips_when_disabled(self):
        app = self._app(email_enabled=False)
        with patch.object(aueye.threading, 'Thread') as thread_cls:
            app._send_alert_email("主题", "正文")
        thread_cls.assert_not_called()

    def test_skips_when_credentials_incomplete(self):
        for missing in ("email_sender", "email_password", "email_receiver"):
            app = self._app(**{missing: ""})
            with patch.object(aueye.threading, 'Thread') as thread_cls:
                app._send_alert_email("主题", "正文")
            thread_cls.assert_not_called()


# ------------------------------------------------------------------ 提醒重复邮件
class TestCheckAlertRepeatEmail(unittest.TestCase):
    """价格停留阈值区间时按 email_interval_sec 重复发邮件。"""

    def setUp(self):
        self.app = _bare_app()
        self.app.au_upper_target = 1000.0
        self.app.au_lower_target = 900.0
        self.app.au_upper_triggered = False
        self.app.au_lower_triggered = False
        self.app.au_upper_last_ts = 0.0
        self.app.au_lower_last_ts = 0.0
        self.app.email_au_upper_last_ts = 0.0
        self.app.email_au_lower_last_ts = 0.0
        self.app._notify = MagicMock()
        self.app._play_sound = MagicMock()
        self.app._send_alert_email = MagicMock()

    def test_repeat_email_sent_after_interval(self):
        """已触发且间隔已过 → 只发邮件，不重复通知/声音。"""
        self.app.au_upper_triggered = True
        self.app.email_au_upper_last_ts = time.time() - 601
        before = self.app.email_au_upper_last_ts
        self.app._check_alert(1005.0, "AU")
        self.app._send_alert_email.assert_called_once()
        self.assertIn("持续上破", self.app._send_alert_email.call_args[0][1])
        self.assertGreater(self.app.email_au_upper_last_ts, before)
        self.app._notify.assert_not_called()
        self.app._play_sound.assert_not_called()

    def test_repeat_email_skipped_before_interval(self):
        self.app.au_upper_triggered = True
        self.app.email_au_upper_last_ts = time.time() - 10
        self.app.email_interval_sec = 600
        self.app._check_alert(1005.0, "AU")
        self.app._send_alert_email.assert_not_called()

    def test_first_cross_syncs_email_timestamp(self):
        """首次穿越后邮件时间戳应与触发时间戳同步，避免立刻重复发。"""
        self.app._check_alert(1005.0, "AU")
        self.assertAlmostEqual(self.app.au_upper_last_ts,
                               self.app.email_au_upper_last_ts, places=3)
        self.assertTrue(self.app.au_upper_triggered)

    def test_lower_repeat_email(self):
        self.app.au_lower_triggered = True
        self.app.email_au_lower_last_ts = time.time() - 601
        self.app._check_alert(895.0, "AU")
        self.app._send_alert_email.assert_called_once()
        self.assertIn("持续下破", self.app._send_alert_email.call_args[0][1])

    def test_cooldown_blocks_first_cross(self):
        """冷却期内不重复通知，但触发锁仍置位。"""
        self.app.au_upper_last_ts = time.time() - 1
        self.app._check_alert(1005.0, "AU")
        self.app._notify.assert_not_called()
        self.assertTrue(self.app.au_upper_triggered)

    def test_target_none_is_skipped(self):
        self.app.au_upper_target = None
        self.app.au_lower_target = None
        self.app._check_alert(1005.0, "AU")
        self.app._notify.assert_not_called()
        self.app._send_alert_email.assert_not_called()

    def test_unknown_symbol_ignored(self):
        self.app._check_alert(1005.0, "UNKNOWN")
        self.app._notify.assert_not_called()


# ------------------------------------------------------------------ 位置持久化
class TestWindowPosition(unittest.TestCase):
    """拖拽后位置的保存与加载。"""

    def test_save_position_writes_config(self):
        app = _bare_app()
        app.root.winfo_x.return_value = 1234
        app.root.winfo_y.return_value = 567
        app.save_config = MagicMock()
        app._save_position()
        self.assertEqual(app.config["window_x"], 1234)
        self.assertEqual(app.config["window_y"], 567)
        app.save_config.assert_called_once_with()

    def test_save_position_swallows_errors(self):
        app = _bare_app()
        app.root.winfo_x.side_effect = RuntimeError("window gone")
        app.save_config = MagicMock()
        app._save_position()   # 不应抛异常
        app.save_config.assert_not_called()

    def test_load_position_returns_tuple(self):
        app = _bare_app()
        app.config = {"window_x": 100, "window_y": 200}
        self.assertEqual(app._load_position(), (100, 200))

    def test_load_position_missing_returns_none(self):
        app = _bare_app()
        app.config = {}
        self.assertIsNone(app._load_position())

    def test_load_position_partial_returns_none(self):
        app = _bare_app()
        app.config = {"window_x": 100}
        self.assertIsNone(app._load_position())

    def test_load_position_bad_value_returns_none(self):
        app = _bare_app()
        app.config = {"window_x": "abc", "window_y": 200}
        self.assertIsNone(app._load_position())

    def test_load_position_string_numbers_ok(self):
        app = _bare_app()
        app.config = {"window_x": "10", "window_y": "20"}
        self.assertEqual(app._load_position(), (10, 20))


# ------------------------------------------------------------------ 字体回退
class TestFindFontFallback(unittest.TestCase):
    def tearDown(self):
        aueye._FONT_CACHE.clear()

    def test_falls_back_to_load_default_when_truetype_fails(self):
        """load_default internally calls truetype(BytesIO), so only string
        paths are forced to fail."""
        aueye._FONT_CACHE.clear()
        real_truetype = aueye.ImageFont.truetype

        def fake_truetype(font=None, size=10, *a, **kw):
            if isinstance(font, str):
                raise OSError("font file unusable")
            return real_truetype(font, size, *a, **kw)

        with patch.object(aueye.os.path, 'exists', return_value=False), \
             patch.object(aueye.ImageFont, 'truetype', side_effect=fake_truetype):
            font = aueye._find_font(size=12, bold=False)
        self.assertIsNotNone(font)
        self.assertIn((12, False), aueye._FONT_CACHE)

    def test_uses_first_existing_candidate(self):
        aueye._FONT_CACHE.clear()
        real_truetype = aueye.ImageFont.truetype
        seen = []

        def spy(path, size, *a, **kw):
            seen.append(path)
            return real_truetype(path, size, *a, **kw)

        with patch.object(aueye.ImageFont, 'truetype', side_effect=spy):
            aueye._find_font(size=20, bold=True)
        self.assertTrue(seen)
        self.assertTrue(seen[0].lower().endswith(('.ttf', '.ttc')))

# ------------------------------------------------------------------ winsound 导入降级
class TestWinsoundImportFallback(unittest.TestCase):
    """winsound 导入失败时静默降级为 None（源码 23-24 行）。"""

    def test_fallback_and_module_restore(self):
        saved = sys.modules.get('winsound')
        sys.modules['winsound'] = None  # import 此时会抛 ImportError
        try:
            importlib.reload(aueye)
            self.assertIsNone(aueye.winsound)
        finally:
            if saved is None:
                sys.modules.pop('winsound', None)
            else:
                sys.modules['winsound'] = saved
            importlib.reload(aueye)
            # reload 会重置模块级覆盖，必须恢复测试用的临时配置路径
            aueye._config_path = lambda: os.path.join(tempfile.gettempdir(), 'test_config.json')
        self.assertIs(aueye.winsound, sys.modules.get('winsound'))
        self.assertTrue(aueye._config_path().endswith('test_config.json'))


# ------------------------------------------------------------------ 字体：候选/最终回退异常
class TestFindFontException(unittest.TestCase):
    """候选字体加载全部失败时：循环内 except pass + 最终 load_default 回退。"""

    def tearDown(self):
        aueye._FONT_CACHE.clear()

    def test_all_candidates_fail_then_load_default(self):
        aueye._FONT_CACHE.clear()
        real_truetype = aueye.ImageFont.truetype

        def fake_truetype(font=None, size=10, *a, **kw):
            if isinstance(font, str):
                raise OSError("font unusable")
            return real_truetype(font, size, *a, **kw)

        with patch.object(aueye.os.path, 'exists', return_value=True), \
             patch.object(aueye.ImageFont, 'truetype', side_effect=fake_truetype):
            font = aueye._find_font(size=11, bold=True)
        self.assertIsNotNone(font)
        self.assertIn((11, True), aueye._FONT_CACHE)


# ------------------------------------------------------------------ 托盘图标渲染
class TestTrayIconRendering(unittest.TestCase):
    """_make_tray_icon：配色、透明圆形、文字内容、字号与 textbbox 异常回退。"""

    def setUp(self):
        self.app = _bare_app()

    def test_up_color(self):
        img = self.app._make_tray_icon(price=931.5, prev_price=930.0)
        self.assertEqual(img.getpixel((4, 128)), (172, 38, 44, 255))

    def test_down_color(self):
        img = self.app._make_tray_icon(price=930.0, prev_price=931.5)
        self.assertEqual(img.getpixel((4, 128)), (26, 124, 62, 255))

    def test_flat_color(self):
        img = self.app._make_tray_icon(price=931.5, prev_price=931.5)
        self.assertEqual(img.getpixel((4, 128)), (52, 57, 76, 255))

    def test_none_prev_color(self):
        img = self.app._make_tray_icon(price=931.5, prev_price=None)
        self.assertEqual(img.getpixel((4, 128)), (52, 57, 76, 255))

    def test_circle_inside_opaque_corner_transparent(self):
        img = self.app._make_tray_icon(price=931.5, prev_price=930.0)
        self.assertEqual(img.getpixel((4, 128))[3], 255)   # 圆内
        self.assertEqual(img.getpixel((1, 1))[3], 0)        # 圆外透明

    def test_price_splits_into_two_lines(self):
        texts = []
        real_text = aueye.ImageDraw.ImageDraw.text

        def spy(self_, xy, text, *a, **kw):
            texts.append(text)
            return real_text(self_, xy, text, *a, **kw)

        with patch.object(aueye.ImageDraw.ImageDraw, 'text', new=spy):
            self.app._make_tray_icon(price=93.5, prev_price=93.0)  # -> "0094"
        # 每行画两次（描边 + 填充）
        self.assertEqual(texts, ["00", "00", "94", "94"])

    def test_none_price_shows_dashes(self):
        texts = []
        real_text = aueye.ImageDraw.ImageDraw.text

        def spy(self_, xy, text, *a, **kw):
            texts.append(text)
            return real_text(self_, xy, text, *a, **kw)

        with patch.object(aueye.ImageDraw.ImageDraw, 'text', new=spy):
            self.app._make_tray_icon(price=None, prev_price=None)
        self.assertEqual(texts, ["--", "--", "", ""])

    def test_textbbox_exception_falls_back(self):
        with patch.object(aueye.ImageDraw.ImageDraw, 'textbbox',
                          side_effect=RuntimeError("no bbox")):
            img = self.app._make_tray_icon(price=931.5, prev_price=930.0)
        self.assertEqual(img.size, (256, 256))

    def test_font_size_and_bold(self):
        real_find_font = aueye._find_font
        with patch.object(aueye, '_find_font',
                          side_effect=lambda **kw: real_find_font(**kw)) as m:
            self.app._make_tray_icon(price=931.5, prev_price=930.0)
        m.assert_called_with(size=153, bold=True)


# ------------------------------------------------------------------ 托盘注册与回调
class TestTraySetupAndCallbacks(unittest.TestCase):
    """_setup_tray、托盘菜单回调、显示切换、设置窗口与退出。"""

    def test_setup_tray_registers_icon_menu_and_thread(self):
        app = _bare_app()
        app._make_tray_icon = MagicMock(return_value='init-icon')
        with patch.object(aueye.pystray, 'Icon') as icon_cls, \
             patch.object(aueye.threading, 'Thread') as thr:
            app._setup_tray()
        args = icon_cls.call_args[0]
        self.assertEqual(args[0], 'AuEye')
        self.assertEqual(args[1], 'init-icon')
        self.assertEqual(args[2], 'AuEye')
        self.assertEqual(len(args[3]), 3)  # 显示/隐藏、设置、退出
        self.assertIs(app.tray, icon_cls.return_value)
        thr.assert_called_once_with(target=icon_cls.return_value.run, daemon=True)
        thr.return_value.start.assert_called_once()

    def test_tray_callbacks_schedule_main_thread(self):
        for cb, expected in ((aueye.GoldTaskbarDoubleLine._tray_toggle, 'toggle_visible'),
                             (aueye.GoldTaskbarDoubleLine._tray_settings, 'open_settings'),
                             (aueye.GoldTaskbarDoubleLine._tray_quit, 'quit_app')):
            app = _bare_app()
            app.root.after = MagicMock()
            cb(app, None, None)
            app.root.after.assert_called_once_with(
                0, getattr(app, expected))

    def test_toggle_visible(self):
        app = _bare_app()
        app.hidden = True
        app.toggle_visible()
        app.root.deiconify.assert_called_once()
        self.assertFalse(app.hidden)
        app.toggle_visible()
        app.root.withdraw.assert_called_once()
        self.assertTrue(app.hidden)

    def test_open_settings_reuses_existing_window(self):
        app = _bare_app()
        app.settings_window = MagicMock()
        app.settings_window.winfo_exists.return_value = True
        with patch.object(aueye, 'SettingsWindow') as sw:
            app.open_settings()
        sw.assert_not_called()
        app.settings_window.deiconify.assert_called_once()
        app.settings_window.lift.assert_called_once()
        app.settings_window.focus_force.assert_called_once()

    def test_open_settings_creates_when_missing(self):
        app = _bare_app()
        app.settings_window = None
        with patch.object(aueye, 'SettingsWindow') as sw:
            app.open_settings()
        sw.assert_called_once_with(app.root, app)
        self.assertIs(app.settings_window, sw.return_value)

    def test_quit_app_swallows_tray_stop_error(self):
        app = _bare_app()
        app.tray = MagicMock()
        app.tray.stop.side_effect = RuntimeError("gone")
        app.quit_app()
        app.root.quit.assert_called_once()
        app.root.destroy.assert_called_once()

    def test_quit_app_without_tray(self):
        app = _bare_app()
        app.tray = None
        app.quit_app()
        app.root.quit.assert_called_once()
        app.root.destroy.assert_called_once()

    def test_quit_app_swallows_root_error(self):
        # root.quit 抛异常（如窗口已销毁）时应静默吞掉，不向外传播
        app = _bare_app()
        app.tray = None
        app.root.quit.side_effect = tk.TclError("destroyed")
        app.quit_app()
        app.root.quit.assert_called_once()
        app.root.destroy.assert_not_called()


# ------------------------------------------------------------------ 卡片绘制（真实 Canvas）
class TestCardDraw(unittest.TestCase):
    """_draw_card / _apply_card_style / _reset_flash_style：真实 Tk Canvas。"""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = _bare_app(root=self.root)
        self.app.canvas = tk.Canvas(self.root, width=220, height=130)
        self.app.canvas.pack()
        self.app.card_width = 220
        self.app.card_height = 130
        self.app.corner_radius = 15
        self.app.card_color = "#101218"
        self.app.card_border = "#2A2D36"
        self.app.text_color = "#F5F5F7"
        self.app.font_family = "Segoe UI"
        self.app.flash_active = True
        self.app.au_value_text = self.app.canvas.create_text(10, 10, text="931.50")
        self.app.au_icon = self.app.canvas.create_text(10, 30, text="浙商")

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def test_draw_card_counts_and_divider(self):
        self.app._draw_card()
        self.assertEqual(len(self.app.card_fill_items), 6)
        self.assertEqual(len(self.app.card_border_items), 5)
        divider = self.app.card_border_items[-1]
        self.assertEqual(self.app.canvas.itemcget(divider, 'dash'), '3 3')

    def test_redraw_clears_previous_items(self):
        self.app._draw_card()
        self.app._draw_card()  # 再画一次：先删除旧图元，数量保持不变
        self.assertEqual(len(self.app.card_fill_items), 6)
        self.assertEqual(len(self.app.card_border_items), 5)

    def test_apply_card_style(self):
        self.app._draw_card()
        self.app._apply_card_style('#111111', '#222222')
        for it in self.app.card_fill_items:
            self.assertEqual(self.app.canvas.itemcget(it, 'fill'), '#111111')
        for it in self.app.card_border_items:
            self.assertEqual(self.app.canvas.itemcget(it, 'fill'), '#222222')

    def test_reset_flash_style(self):
        self.app._draw_card()
        self.app._reset_flash_style()
        self.assertEqual(
            self.app.canvas.itemcget(self.app.au_value_text, 'fill'), '#F5F5F7')
        self.assertEqual(
            self.app.canvas.itemcget(self.app.au_icon, 'fill'), '#E8B84B')
        self.assertFalse(self.app.flash_active)


# ------------------------------------------------------------------ 折线：异常分支
class TestSparklineExtra(unittest.TestCase):
    """_draw_sparkline：旧图元删除异常吞掉、create_text 异常吞掉。"""

    def _base_app(self, **attrs):
        app = _bare_app(**attrs)
        app.canvas = MagicMock()
        app._price_lock = aueye.threading.Lock()
        app._timesharing_data = []
        app._timesharing_pre_close = None
        app._sparkline_item_ids = []
        app._sparkline_visible = True
        app.card_width = 220
        app.card_height = 130
        app.muted_color = "#9AA0A6"
        app.up_color = "#FF3B30"
        app.down_color = "#00C853"
        app.font_family = "Segoe UI"
        return app

    def test_delete_exception_swallowed_and_too_little_data(self):
        app = self._base_app()
        app._sparkline_item_ids = [12345]
        app.canvas.delete.side_effect = RuntimeError("bad item")
        app._draw_sparkline()  # 不应抛异常
        self.assertFalse(app._sparkline_visible)
        self.assertEqual(app._sparkline_item_ids, [])

    def test_create_text_exception_swallowed(self):
        app = self._base_app()
        app._timesharing_data = [{"price": 930.0, "time": 0},
                                 {"price": 932.0, "time": 1}]
        app.canvas.create_text.side_effect = RuntimeError("no text")
        app._draw_sparkline()
        self.assertTrue(app._sparkline_visible)
        self.assertTrue(app.canvas.create_line.called)


# ------------------------------------------------------------------ 拖拽
class TestDrag(unittest.TestCase):
    """_bind_drag / _start_drag / _on_drag / _end_drag。"""

    def test_bind_drag_binds_three_events_on_canvas_and_root(self):
        app = _bare_app()
        app.canvas = MagicMock()
        app._bind_drag()
        seqs_c = [c.args[0] for c in app.canvas.bind.call_args_list]
        seqs_r = [c.args[0] for c in app.root.bind.call_args_list]
        expect = ["<ButtonPress-1>", "<B1-Motion>", "<ButtonRelease-1>"]
        self.assertEqual(seqs_c, expect)
        self.assertEqual(seqs_r, expect)

    def test_start_drag_computes_offset(self):
        app = _bare_app()
        app.root.winfo_x.return_value = 100
        app.root.winfo_y.return_value = 200
        event = MagicMock(x_root=150, y_root=260)
        app._start_drag(event)
        self.assertEqual(app.drag_offset_x, 50)
        self.assertEqual(app.drag_offset_y, 60)

    def test_on_drag_sets_geometry(self):
        app = _bare_app()
        app.card_width = 220
        app.card_height = 130
        app.drag_offset_x = 50
        app.drag_offset_y = 60
        event = MagicMock(x_root=300, y_root=400)
        app._on_drag(event)
        app.root.geometry.assert_called_once_with("220x130+250+340")

    def test_end_drag_cancels_previous_timer(self):
        app = _bare_app()
        app.root.after.return_value = 'T1'
        app._end_drag(MagicMock())
        self.assertEqual(app._save_timer, 'T1')
        app.root.after_cancel.assert_not_called()
        app.root.after.return_value = 'T2'
        app._end_drag(MagicMock())
        app.root.after_cancel.assert_called_once_with('T1')
        self.assertEqual(app._save_timer, 'T2')


# ------------------------------------------------------------------ 闪烁：奇数余数分支
class TestFlashTextOddRem(unittest.TestCase):
    """同步驱动 root.after，让 toggle 走完奇数余数（else 分支）与复位。"""

    def setUp(self):
        self.app = _bare_app()
        self.app.canvas = MagicMock()
        self.app.root = MagicMock()
        # 同步执行回调，递归驱动 toggle 直到结束
        self.app.root.after = lambda ms, cb=None, *a: cb() if cb is not None else None
        self.app.au_value_text = 77
        self.app.au_icon = 78
        self.app.flash_active = False
        self.app._flash_after_id = None
        self.app.text_color = '#F5F5F7'
        self.app.font_family = 'Segoe UI'
        self.app.flash_text_font_size = 12
        self.app.card_color = '#101218'
        self.app.card_border = '#2A2D36'
        self.app.card_highlight_color = '#1B1F2A'
        self.app.card_border_highlight = '#3A4150'
        self.app.extreme_flash_interval_ms = 150
        self.app.card_fill_items = [1]
        self.app.card_border_items = [2]
        self.app._apply_card_style = MagicMock()
        # 必须真正清除 flash_active，否则 flash_active 永远为 True
        self.app._reset_flash_style = MagicMock(
            side_effect=lambda: setattr(self.app, 'flash_active', False))

    def test_odd_remainder_toggles_and_resets(self):
        self.app._flash_text('#FF0000', 1)  # total=2 -> 两次 toggle + 复位
        self.app._apply_card_style.assert_any_call(
            self.app.card_highlight_color, self.app.card_border_highlight)
        # 奇数余数走 else 分支：恢复卡片常规配色
        self.app._apply_card_style.assert_any_call(
            self.app.card_color, self.app.card_border)
        self.app._reset_flash_style.assert_called_once()
        self.assertFalse(self.app.flash_active)
        fills = [c.kwargs.get('fill') for c in self.app.canvas.itemconfig.call_args_list]
        self.assertIn('#FF0000', fills)
        self.assertIn('#F5F5F7', fills)


# ------------------------------------------------------------------ 设置窗口
class TestSettingsWindow(unittest.TestCase):
    """SettingsWindow：真实 Toplevel + 校验分支 + 保存 + 测试邮件 + 关闭。"""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        app = MagicMock()
        app.interval = 2
        app.root.attributes.return_value = "0.55"
        app.sound_enabled = True
        app.log_enabled = False
        app.au_upper_target = None
        app.au_lower_target = 900.0
        app.extreme_enabled = True
        app.extreme_window_sec = 300
        app.extreme_threshold = 5.0
        app.extreme_flash_times = 6
        app.extreme_flash_interval_ms = 150
        app.extreme_cooldown_sec = 60
        app.email_enabled = False
        app.email_sender = ""
        app.email_password = ""
        app.email_receiver = ""
        app.smtp_host = "smtp.163.com"
        app.smtp_port = 465
        app.smtp_ssl = True
        app.email_interval_sec = 600
        app.settings_window = None
        self.app = app
        self.win = aueye.SettingsWindow(self.root, app)

    def tearDown(self):
        try:
            self.win.destroy()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _save_expect_error(self, msg, setter):
        with patch.object(aueye.messagebox, 'showerror') as se:
            setter()
            self.win._save()
        se.assert_called_once()
        self.assertEqual(se.call_args[0], ("错误", msg))
        self.assertIs(se.call_args.kwargs.get('parent'), self.win)
        self.app.apply_config.assert_not_called()

    def test_save_bad_interval(self):
        self._save_expect_error("刷新间隔必须是有效的正数",
                                lambda: self.win.var_interval.set("0"))

    def test_save_bad_opacity(self):
        self._save_expect_error("透明度必须在 0.2 到 1.0 之间",
                                lambda: self.win.var_opacity.set(0.1))

    def test_save_bad_window_min(self):
        self._save_expect_error("统计窗口必须是有效的正整数（分钟）",
                                lambda: self.win.var_window_min.set("abc"))

    def test_save_bad_threshold(self):
        self._save_expect_error("异动阈值必须是有效的正数",
                                lambda: self.win.var_extreme_threshold.set("0"))

    def test_save_bad_flash_times(self):
        self._save_expect_error("闪烁次数必须是 1~50 的整数",
                                lambda: self.win.var_flash_times.set("99"))

    def test_save_bad_flash_interval(self):
        self._save_expect_error("闪烁间隔必须是不小于 50 的整数（毫秒）",
                                lambda: self.win.var_flash_interval.set("10"))

    def test_save_bad_cooldown(self):
        self._save_expect_error("冷却时间必须是有效的正整数（秒）",
                                lambda: self.win.var_cooldown.set("0"))

    def test_save_bad_smtp_port(self):
        self._save_expect_error("SMTP 端口必须是 1~65535 的整数",
                                lambda: self.win.var_smtp_port.set("70000"))

    def test_save_bad_email_interval(self):
        self._save_expect_error("邮件提醒间隔必须 ≥ 5 分钟",
                                lambda: self.win.var_email_interval.set("3"))

    def test_save_success_applies_config(self):
        with patch.object(aueye.messagebox, 'showinfo') as si:
            self.win._save()
        self.app.apply_config.assert_called_once()
        cfg = self.app.apply_config.call_args[0][0]
        self.assertEqual(cfg["refresh_interval"], 2.0)
        self.assertEqual(cfg["opacity"], 0.55)
        self.assertEqual(cfg["email_interval_sec"], 600)
        self.assertFalse(cfg["au_upper_enabled"])
        self.assertEqual(cfg["au_upper_value"], 0.0)
        self.assertTrue(cfg["au_lower_enabled"])
        self.assertEqual(cfg["au_lower_value"], 900.0)
        self.assertEqual(cfg["extreme_window_min"], 5)
        self.assertEqual(cfg["smtp_port"], 465)
        self.app.save_config.assert_called_once()
        si.assert_called_once_with("成功", "设置已保存并应用", parent=self.win)

    def test_save_email_interval_minutes_to_seconds(self):
        self.win.var_email_interval.set("7")
        with patch.object(aueye.messagebox, 'showinfo'):
            self.win._save()
        cfg = self.app.apply_config.call_args[0][0]
        self.assertEqual(cfg["email_interval_sec"], 420)

    def test_save_upper_enabled_parses_value(self):
        self.win.var_au_upper_enabled.set(True)
        self.win.var_au_upper_value.set("950.5")
        with patch.object(aueye.messagebox, 'showinfo'):
            self.win._save()
        cfg = self.app.apply_config.call_args[0][0]
        self.assertTrue(cfg["au_upper_enabled"])
        self.assertEqual(cfg["au_upper_value"], 950.5)

    def test_save_upper_bad_value_disables(self):
        self.win.var_au_upper_enabled.set(True)
        self.win.var_au_upper_value.set("abc")
        with patch.object(aueye.messagebox, 'showinfo'):
            self.win._save()
        cfg = self.app.apply_config.call_args[0][0]
        self.assertFalse(cfg["au_upper_enabled"])
        self.assertEqual(cfg["au_upper_value"], 0.0)

    def test_test_email_empty_fields(self):
        with patch.object(aueye.messagebox, 'showerror') as se:
            self.win._test_email()
        se.assert_called_once_with(
            "错误", "请先填写发件人、授权码和收件人", parent=self.win)

    def _run_sync_email(self, send_result):
        class _SyncThread:
            last = None

            def __init__(self, target=None, daemon=None, **kw):
                type(self).last = self
                self.target = target
                self.daemon = daemon
                self.started = False

            def start(self):
                self.started = True
                if self.target:
                    self.target()

        self.win.var_email_sender.set("a@b.com")
        self.win.var_email_password.set("token")
        self.win.var_email_receiver.set("c@d.com")
        self.app.send_email = MagicMock(return_value=send_result)
        with patch.object(aueye.threading, 'Thread', _SyncThread):
            self.win._test_email()
            self.win.update()  # 派发 after(0) 回调
        return _SyncThread.last

    def test_test_email_success(self):
        # showinfo/showerror 必须打桩，否则 win.update() 会弹出真实模态对话框并挂起
        with patch.object(aueye.messagebox, 'showinfo'), \
             patch.object(aueye.messagebox, 'showerror'):
            thr = self._run_sync_email((True, ""))
        self.assertTrue(thr.started)
        self.assertTrue(thr.daemon)
        args, kw = self.app.send_email.call_args
        self.assertEqual(args, ("a@b.com", "token", "c@d.com"))
        self.assertIn("subject", kw)
        self.assertIn("body", kw)

    def test_test_email_failure(self):
        with patch.object(aueye.messagebox, 'showerror') as se:
            self._run_sync_email((False, "boom"))
        se.assert_called_once_with("发送失败", "错误信息：boom", parent=self.win)

    def test_test_email_success_shows_info(self):
        # success 分支单独跑，避免 showerror 断言干扰
        with patch.object(aueye.messagebox, 'showinfo') as si:
            self._run_sync_email((True, ""))
        si.assert_called_once_with("成功", "测试邮件发送成功", parent=self.win)

    def test_on_opacity_change(self):
        self.win._on_opacity_change("0.75")
        self.assertEqual(self.win.opacity_value_label.cget('text'), "0.75")
        self.win._on_opacity_change("abc")  # 静默忽略
        self.assertEqual(self.win.opacity_value_label.cget('text'), "0.75")

    def test_on_close_destroys_and_clears(self):
        self.app.settings_window = self.win
        self.win._on_close()
        self.assertIsNone(self.app.settings_window)
        # 销毁后窗口必须不存在；不同 Tk 版本 winfo_exists 可能返回 0 或抛 TclError
        try:
            exists = self.win.winfo_exists()
        except tk.TclError:
            exists = 0
        self.assertFalse(exists)


# ------------------------------------------------------------------ 应用 __init__
class TestAppInit(unittest.TestCase):
    """GoldTaskbarDoubleLine.__init__：真实 Tk + 打桩线程/托盘。"""

    def setUp(self):
        # 配置快照：保证用默认配置初始化，结束后还原
        self._cfg_path = aueye._config_path()
        self._cfg_backup = None
        if os.path.exists(self._cfg_path):
            with open(self._cfg_path, 'rb') as f:
                self._cfg_backup = f.read()
        with open(self._cfg_path, 'w', encoding='utf-8') as f:
            json.dump(aueye.DEFAULT_CONFIG, f)

        class _RecThread:
            instances = []

            def __init__(self, target=None, daemon=None, **kw):
                self.target = target
                self.daemon = daemon
                self.started = False
                type(self).instances.append(self)

            def start(self):
                self.started = True

        self._thr_cls = _RecThread
        self._p_thr = patch.object(aueye.threading, 'Thread', _RecThread)
        self._p_icon = patch.object(aueye.pystray, 'Icon')
        self.icon_mock = self._p_thr and self._p_icon.start()
        self._p_thr.start()
        self.app = aueye.GoldTaskbarDoubleLine()

    def tearDown(self):
        try:
            self.app.root.destroy()
        except Exception:
            pass
        self._p_thr.stop()
        self._p_icon.stop()
        try:
            if self._cfg_backup is None:
                if os.path.exists(self._cfg_path):
                    os.remove(self._cfg_path)
            else:
                with open(self._cfg_path, 'wb') as f:
                    f.write(self._cfg_backup)
        except Exception:
            pass

    def test_hidden_withdrawn_and_defaults(self):
        self.assertTrue(self.app.hidden)
        self.assertEqual(self.app.root.state(), 'withdrawn')
        self.assertIsNone(self.app.settings_window)
        self.assertEqual(len(self.app.card_fill_items), 6)
        self.assertEqual(len(self.app.card_border_items), 5)

    def test_tray_created_via_patched_icon(self):
        self.assertIs(self.app.tray, self.icon_mock.return_value)

    def test_threads_started_as_daemons(self):
        insts = self._thr_cls.instances
        # 创建顺序：托盘线程 → 数据抓取 → 分时走势
        self.assertGreaterEqual(len(insts), 3)
        self.assertEqual(insts[0].target, self.icon_mock.return_value.run)
        self.assertEqual(insts[1].target, self.app._data_fetch_loop)
        self.assertEqual(insts[2].target, self.app._fetch_timesharing)
        for t in insts:
            self.assertTrue(t.daemon)
            self.assertTrue(t.started)
        self.assertIs(self.app.data_thread, insts[1])

    def test_card_geometry_positioned(self):
        geo = self.app.root.geometry()
        self.assertTrue(geo.startswith("220x130+"), geo)

    def test_saved_position_used(self):
        # 配置中存在 window_x/window_y 时，__init__ 应使用保存的位置（aueye.py:457）
        with open(self._cfg_path, 'w', encoding='utf-8') as f:
            json.dump({**aueye.DEFAULT_CONFIG, "window_x": 100, "window_y": 200}, f)
        app2 = aueye.GoldTaskbarDoubleLine()
        try:
            self.assertTrue(app2.root.geometry().endswith("+100+200"),
                            app2.root.geometry())
        finally:
            try:
                app2.root.destroy()
            except Exception:
                pass


# ------------------------------------------------------------------ UI 主循环
class TestUiCycle(unittest.TestCase):
    """_update_ui_cycle：队列消费、箭头方向、折线节流、托盘刷新。"""

    def _mk(self, **kw):
        app = _bare_app(**kw)
        app.canvas = MagicMock()
        app._action_queue = queue.Queue()
        app._price_lock = aueye.threading.Lock()
        app.au = None
        app.prev_au = None
        app.au_value_text = 77
        app.au_arrow_text = 78
        app.up_color = "#FF3B30"
        app.down_color = "#00C853"
        app.muted_color = "#9AA0A6"
        app.tray = None
        app._flash_text = MagicMock()
        return app

    def _itemconfig_texts(self, app):
        return {c.args[0]: c.kwargs.get('text')
                for c in app.canvas.itemconfig.call_args_list
                if c.args and isinstance(c.args[0], int)}

    def test_flash_action_dispatched(self):
        app = self._mk()
        app._action_queue.put(("flash", "#FF0000", 3))
        app._update_ui_cycle()
        app._flash_text.assert_called_once_with("#FF0000", 3)

    def test_empty_queue_ignored(self):
        app = self._mk()
        app._update_ui_cycle()  # queue.Empty 被吞掉
        app._flash_text.assert_not_called()

    def test_no_price_no_itemconfig(self):
        app = self._mk()
        app._update_ui_cycle()
        called_ids = [c.args[0] for c in app.canvas.itemconfig.call_args_list
                      if c.args]
        self.assertNotIn(77, called_ids)
        self.assertNotIn(78, called_ids)

    def test_price_without_prev_no_arrow(self):
        app = self._mk()
        app.au = 931.5
        app._update_ui_cycle()
        self.assertEqual(self._itemconfig_texts(app).get(77), "931.50")
        called_ids = [c.args[0] for c in app.canvas.itemconfig.call_args_list
                      if c.args]
        self.assertNotIn(78, called_ids)

    def _arrow_case(self, au, prev, expect_text, expect_fill):
        app = self._mk()
        app.au, app.prev_au = au, prev
        app._update_ui_cycle()
        arrow_calls = [c for c in app.canvas.itemconfig.call_args_list
                       if c.args and c.args[0] == 78]
        self.assertEqual(len(arrow_calls), 1)
        self.assertEqual(arrow_calls[0].kwargs.get('text'), expect_text)
        self.assertEqual(arrow_calls[0].kwargs.get('fill'), expect_fill)

    def test_arrow_up(self):
        self._arrow_case(931.5, 930.0, "↑", "#FF3B30")

    def test_arrow_down(self):
        self._arrow_case(930.0, 931.5, "↓", "#00C853")

    def test_arrow_flat(self):
        self._arrow_case(931.5, 931.5, "•", "#9AA0A6")

    def test_sparkline_every_ten_ticks(self):
        app = self._mk()
        app._sparkline_tick = 9
        app._draw_sparkline = MagicMock()
        app._update_ui_cycle()
        app._draw_sparkline.assert_called_once()
        self.assertEqual(app._sparkline_tick, 0)

    def test_sparkline_error_swallowed(self):
        app = self._mk()
        app._sparkline_tick = 9
        app._draw_sparkline = MagicMock(side_effect=RuntimeError("x"))
        app._update_ui_cycle()  # 不应抛异常
        self.assertEqual(app._sparkline_tick, 0)

    def test_tray_none_skips_tray_block(self):
        app = self._mk()
        app._make_tray_icon = MagicMock()
        app._update_ui_cycle()
        app._make_tray_icon.assert_not_called()

    def test_tray_invisible_skips(self):
        app = self._mk()
        app.tray = MagicMock(visible=False)
        app._make_tray_icon = MagicMock(return_value='IMG')
        app._update_ui_cycle()
        app._make_tray_icon.assert_not_called()

    def test_tray_refresh_and_title(self):
        app = self._mk()
        app.tray = MagicMock(visible=True)
        app._make_tray_icon = MagicMock(return_value='IMG')
        app.au = 931.5
        app._update_ui_cycle()
        self.assertEqual(app.tray.icon, 'IMG')
        self.assertEqual(app.tray.title, "浙商金 931.50")
        # 同一签名不重复渲染
        app._update_ui_cycle()
        self.assertEqual(app._make_tray_icon.call_count, 1)

    def test_tray_title_without_price(self):
        app = self._mk()
        app.tray = MagicMock(visible=True)
        app._make_tray_icon = MagicMock(return_value='IMG')
        app._update_ui_cycle()
        self.assertEqual(app.tray.title, "浙商金")

    def test_tray_render_error_swallowed(self):
        app = self._mk()
        app.tray = MagicMock(visible=True)
        app._make_tray_icon = MagicMock(side_effect=RuntimeError("x"))
        app._update_ui_cycle()  # 不应抛异常
        self.assertFalse(hasattr(app, '_tray_sig'))

    def test_always_reschedules_next_cycle(self):
        app = self._mk()
        app._update_ui_cycle()
        app.root.after.assert_called_with(200, app._update_ui_cycle)


# ------------------------------------------------------------------ 抓取循环
class TestFetchLoop(unittest.TestCase):
    """_data_fetch_loop：成功/失败退避/异常日志/中断传播/分时节流。"""

    def _mk(self):
        app = _bare_app()
        app._price_lock = aueye.threading.Lock()
        app.au = 930.0
        app.prev_au = 929.0
        app._fetch_fail_count = 0
        app._check_alert = MagicMock()
        app._track_au_extreme = MagicMock()
        app._fetch_timesharing = MagicMock()
        app._fetch_au = MagicMock(return_value=931.5)
        return app

    def _run(self, app):
        with patch.object(aueye.time, 'sleep', side_effect=KeyboardInterrupt), \
             patch.object(aueye.random, 'uniform', return_value=0.2):
            with self.assertRaises(KeyboardInterrupt):
                app._data_fetch_loop()

    def test_success_updates_price_and_alerts(self):
        app = self._mk()
        self._run(app)
        self.assertEqual(app.au, 931.5)
        self.assertEqual(app.prev_au, 930.0)
        self.assertEqual(app._fetch_fail_count, 0)
        app._check_alert.assert_called_once_with(931.5, "AU")
        app._track_au_extreme.assert_called_once_with(931.5)

    def test_success_sleeps_interval_plus_jitter(self):
        app = self._mk()
        with patch.object(aueye.time, 'sleep', side_effect=KeyboardInterrupt) as sl, \
             patch.object(aueye.random, 'uniform', return_value=0.2):
            with self.assertRaises(KeyboardInterrupt):
                app._data_fetch_loop()
        sl.assert_called_once_with(2.2)

    def test_none_price_backs_off(self):
        app = self._mk()
        app._fetch_au.return_value = None
        with patch.object(aueye.time, 'sleep', side_effect=KeyboardInterrupt) as sl, \
             patch.object(aueye.random, 'uniform', return_value=0.2):
            with self.assertRaises(KeyboardInterrupt):
                app._data_fetch_loop()
        self.assertEqual(app._fetch_fail_count, 1)
        self.assertEqual(app.au, 930.0)  # 价格不变
        app._check_alert.assert_not_called()
        sl.assert_called_once_with(4.2)  # min(2 * 2**1, 60) + 0.2

    def test_exception_logged_and_counted(self):
        app = self._mk()
        app._fetch_au.side_effect = RuntimeError("boom")
        with patch.object(aueye.log, 'error') as log_err, \
             patch.object(aueye.time, 'sleep', side_effect=KeyboardInterrupt), \
             patch.object(aueye.random, 'uniform', return_value=0.2):
            with self.assertRaises(KeyboardInterrupt):
                app._data_fetch_loop()
        log_err.assert_called_once_with("Data fetch loop error: boom")
        self.assertEqual(app._fetch_fail_count, 1)

    def test_keyboard_interrupt_propagates(self):
        app = self._mk()
        app._fetch_au.side_effect = KeyboardInterrupt
        with patch.object(aueye.time, 'sleep'):
            with self.assertRaises(KeyboardInterrupt):
                app._data_fetch_loop()
        self.assertEqual(app._fetch_fail_count, 0)  # 未被吞掉计数

    def test_timesharing_refresh_and_counter_reset(self):
        app = self._mk()
        app._fetch_au.side_effect = [931.0, 932.0]
        with patch.object(aueye, 'timesharing_needed', return_value=True) as ts, \
             patch.object(aueye.time, 'sleep', side_effect=[None, KeyboardInterrupt]), \
             patch.object(aueye.random, 'uniform', return_value=0.0):
            with self.assertRaises(KeyboardInterrupt):
                app._data_fetch_loop()
        self.assertEqual(app._fetch_timesharing.call_count, 2)
        # 计数器在刷新后归零：两次都以 1 起算
        self.assertEqual([c.args for c in ts.call_args_list], [(1, 2), (1, 2)])


# ------------------------------------------------------------------ run / __main__
class TestRunAndMain(unittest.TestCase):
    """run() 的退出路径 + __main__ 入口（成功与崩溃写 crash.log）。"""

    def test_run_normal_destroys_root(self):
        app = _bare_app()
        app.root = MagicMock()
        app.run()
        app.root.mainloop.assert_called_once()
        app.root.destroy.assert_called_once()

    def test_run_keyboard_interrupt_swallowed(self):
        app = _bare_app()
        app.root = MagicMock()
        app.root.mainloop.side_effect = KeyboardInterrupt
        app.run()  # 不应抛出
        app.root.destroy.assert_called_once()

    def test_run_destroy_error_swallowed(self):
        app = _bare_app()
        app.root = MagicMock()
        app.root.destroy.side_effect = RuntimeError("gone")
        app.run()  # 不应抛出

    def test_main_crash_writes_crash_log(self):
        import shutil
        import runpy
        tmp = tempfile.mkdtemp(prefix='aueye_main_')
        try:
            src = os.path.join(tmp, 'aueye.py')
            shutil.copy2(aueye.__file__, src)
            with patch('tkinter.Tk', side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    runpy.run_path(src, run_name='__main__')
            crash = os.path.join(tmp, 'crash.log')
            self.assertTrue(os.path.exists(crash))
            with open(crash, encoding='utf-8') as f:
                content = f.read()
            self.assertIn("RuntimeError: boom", content)
        finally:
            import shutil as _sh
            _sh.rmtree(tmp, ignore_errors=True)

    def test_main_success_builds_and_runs_app(self):
        import shutil
        import runpy
        import threading as _thr
        import pystray as _py
        tmp = tempfile.mkdtemp(prefix='aueye_main_')
        try:
            src = os.path.join(tmp, 'aueye.py')
            shutil.copy2(aueye.__file__, src)
            with patch('tkinter.Tk') as tk_mock, \
                 patch('tkinter.Canvas') as canvas_mock, \
                 patch.object(_py, 'Icon') as icon_mock, \
                 patch.object(_thr, 'Thread') as thr_mock:
                ns = runpy.run_path(src, run_name='__main__')
            self.assertTrue(tk_mock.called)
            self.assertTrue(canvas_mock.called)
            app = ns['app']
            self.assertIs(app.tray, icon_mock.return_value)
            # 托盘 + 数据抓取 + 分时走势 = 3 个线程
            self.assertEqual(thr_mock.call_count, 3)
            app.root.mainloop.assert_called_once()
            self.assertFalse(os.path.exists(os.path.join(tmp, 'crash.log')))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
