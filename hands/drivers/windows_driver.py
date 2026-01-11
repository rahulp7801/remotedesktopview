"""
WindowsACI Driver for Agent-S

Implements Windows-specific GUI automation using:
- WindowsACI: Vision-based element detection from Agent-S
- Win32 APIs: Screen capture and event injection
- UI Automation: Application window management

This driver provides the low-level OS interface for Agent-S on Windows.
"""

import subprocess
import sys
from typing import Optional, Tuple, List

from loguru import logger


class WindowsDriver:
    """
    Windows GUI automation driver using WindowsACI.

    Handles:
    - Screen capture via Win32 GDI
    - Mouse and keyboard events via SendInput
    - Application focus via Win32 SetForegroundWindow
    - Window management via UI Automation
    """

    def __init__(self):
        """Initialize Windows driver and validate platform."""
        if sys.platform != "win32":
            raise RuntimeError(f"WindowsDriver requires Windows, got {sys.platform}")

        logger.info("Initializing WindowsDriver")
        self._validate_dependencies()

    def _validate_dependencies(self):
        """
        Validate required Windows dependencies.

        Checks:
        - PowerShell available (for some operations)
        - pywin32 installed (optional, for native Win32 APIs)
        """
        logger.debug("Validating Windows dependencies")

        # Check PowerShell availability
        try:
            result = subprocess.run(
                ["powershell", "-Command", "echo test"],
                capture_output=True,
                timeout=5
            )

            if result.returncode != 0:
                logger.warning("PowerShell not available or not working correctly")
        except Exception as e:
            logger.warning(f"Could not verify PowerShell: {e}")

        # Check for pywin32 (optional but recommended)
        try:
            import win32api
            logger.debug("pywin32 available - will use native Win32 APIs")
            self._has_pywin32 = True
        except ImportError:
            logger.info("pywin32 not installed - will use PowerShell fallbacks")
            logger.info("Install with: pip install pywin32")
            self._has_pywin32 = False

    def capture_screen(self, save_path: str) -> str:
        """
        Capture screenshot using Windows APIs.

        Args:
            save_path: Path to save the screenshot

        Returns:
            Path to saved screenshot

        Raises:
            RuntimeError: If screenshot fails
        """
        logger.debug(f"Capturing Windows screenshot to: {save_path}")

        if self._has_pywin32:
            return self._capture_screen_win32(save_path)
        else:
            return self._capture_screen_powershell(save_path)

    def _capture_screen_win32(self, save_path: str) -> str:
        """Capture screenshot using pywin32."""
        try:
            import win32gui
            import win32ui
            import win32con
            from PIL import Image

            # Get screen dimensions
            hdesktop = win32gui.GetDesktopWindow()
            width = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
            height = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
            left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
            top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)

            # Create device context
            desktop_dc = win32gui.GetWindowDC(hdesktop)
            img_dc = win32ui.CreateDCFromHandle(desktop_dc)
            mem_dc = img_dc.CreateCompatibleDC()

            # Create bitmap
            screenshot = win32ui.CreateBitmap()
            screenshot.CreateCompatibleBitmap(img_dc, width, height)
            mem_dc.SelectObject(screenshot)

            # Copy screen to bitmap
            mem_dc.BitBlt((0, 0), (width, height), img_dc, (left, top), win32con.SRCCOPY)

            # Convert to PIL Image and save
            bmpinfo = screenshot.GetInfo()
            bmpstr = screenshot.GetBitmapBits(True)
            img = Image.frombuffer(
                'RGB',
                (bmpinfo['bmWidth'], bmpinfo['bmHeight']),
                bmpstr, 'raw', 'BGRX', 0, 1
            )

            img.save(save_path)

            # Cleanup
            mem_dc.DeleteDC()
            win32gui.DeleteObject(screenshot.GetHandle())
            img_dc.DeleteDC()
            win32gui.ReleaseDC(hdesktop, desktop_dc)

            logger.debug(f"Screenshot saved: {save_path}")
            return save_path

        except Exception as e:
            logger.error(f"Win32 screenshot failed: {e}")
            raise RuntimeError(f"Screenshot capture failed: {e}") from e

    def _capture_screen_powershell(self, save_path: str) -> str:
        """Fallback screenshot using PowerShell."""
        ps_script = f"""
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing

        $screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bitmap = New-Object System.Drawing.Bitmap($screen.Width, $screen.Height)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $graphics.CopyFromScreen($screen.Location, [System.Drawing.Point]::Empty, $screen.Size)
        $bitmap.Save("{save_path}")
        $graphics.Dispose()
        $bitmap.Dispose()
        """

        try:
            result = subprocess.run(
                ["powershell", "-Command", ps_script],
                check=True,
                capture_output=True
            )

            logger.debug(f"Screenshot saved: {save_path}")
            return save_path

        except subprocess.CalledProcessError as e:
            error_msg = f"PowerShell screenshot failed: {e.stderr.decode() if e.stderr else str(e)}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

    def click(self, x: int, y: int):
        """
        Perform mouse click at coordinates.

        Args:
            x: X coordinate
            y: Y coordinate
        """
        logger.debug(f"Clicking at ({x}, {y})")

        if self._has_pywin32:
            self._click_win32(x, y)
        else:
            self._click_powershell(x, y)

    def _click_win32(self, x: int, y: int):
        """Click using pywin32."""
        import win32api
        import win32con

        # Move mouse to position
        win32api.SetCursorPos((x, y))

        # Click (down then up)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)

    def _click_powershell(self, x: int, y: int):
        """Fallback click using PowerShell."""
        ps_script = f"""
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point({x}, {y})

        Add-Type -MemberDefinition @"
        [DllImport("user32.dll")]
        public static extern void mouse_event(int dwFlags, int dx, int dy, int dwData, int dwExtraInfo);
        "@ -Namespace Win32 -Name Mouse

        [Win32.Mouse]::mouse_event(0x02, 0, 0, 0, 0)  # LEFTDOWN
        [Win32.Mouse]::mouse_event(0x04, 0, 0, 0, 0)  # LEFTUP
        """

        subprocess.run(
            ["powershell", "-Command", ps_script],
            check=True,
            capture_output=True
        )

    def type_text(self, text: str):
        """
        Type text at current cursor position.

        Args:
            text: Text to type
        """
        logger.debug(f"Typing text: {text[:50]}...")

        if self._has_pywin32:
            self._type_win32(text)
        else:
            self._type_powershell(text)

    def _type_win32(self, text: str):
        """Type using pywin32."""
        import win32api
        import win32con
        import time

        for char in text:
            # Get virtual key code
            vk = win32api.VkKeyScan(char)

            # Key down
            win32api.keybd_event(vk, 0, 0, 0)
            time.sleep(0.01)

            # Key up
            win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.01)

    def _type_powershell(self, text: str):
        """Fallback typing using PowerShell."""
        # Escape text for PowerShell
        escaped_text = text.replace('"', '`"').replace('$', '`$')

        ps_script = f"""
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.SendKeys]::SendWait("{escaped_text}")
        """

        subprocess.run(
            ["powershell", "-Command", ps_script],
            check=True,
            capture_output=True
        )

    def get_active_window(self) -> Optional[str]:
        """
        Get name of currently active window.

        Returns:
            Window name or None if unable to determine
        """
        logger.debug("Getting active window")

        if self._has_pywin32:
            return self._get_active_window_win32()
        else:
            return self._get_active_window_powershell()

    def _get_active_window_win32(self) -> Optional[str]:
        """Get active window using pywin32."""
        try:
            import win32gui

            hwnd = win32gui.GetForegroundWindow()
            window_name = win32gui.GetWindowText(hwnd)

            logger.debug(f"Active window: {window_name}")
            return window_name

        except Exception as e:
            logger.error(f"Failed to get active window: {e}")
            return None

    def _get_active_window_powershell(self) -> Optional[str]:
        """Fallback get active window using PowerShell."""
        ps_script = """
        Add-Type @"
        using System;
        using System.Runtime.InteropServices;
        using System.Text;
        public class Window {
            [DllImport("user32.dll")]
            public static extern IntPtr GetForegroundWindow();
            [DllImport("user32.dll")]
            public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
        }
        "@

        $handle = [Window]::GetForegroundWindow()
        $title = New-Object System.Text.StringBuilder(256)
        [void][Window]::GetWindowText($handle, $title, 256)
        $title.ToString()
        """

        try:
            result = subprocess.run(
                ["powershell", "-Command", ps_script],
                check=True,
                capture_output=True,
                text=True
            )

            window_name = result.stdout.strip()
            logger.debug(f"Active window: {window_name}")
            return window_name

        except Exception as e:
            logger.error(f"Failed to get active window: {e}")
            return None

    def bring_app_to_front(self, app_name: str):
        """
        Bring application to front (activate).

        Args:
            app_name: Name of application process to activate
        """
        logger.debug(f"Bringing {app_name} to front")

        ps_script = f"""
        $process = Get-Process | Where-Object {{$_.ProcessName -eq "{app_name}" -or $_.MainWindowTitle -like "*{app_name}*"}} | Select-Object -First 1

        if ($process) {{
            Add-Type @"
            using System;
            using System.Runtime.InteropServices;
            public class WinAPI {{
                [DllImport("user32.dll")]
                [return: MarshalAs(UnmanagedType.Bool)]
                public static extern bool SetForegroundWindow(IntPtr hWnd);
            }}
"@
            [WinAPI]::SetForegroundWindow($process.MainWindowHandle)
        }}
        """

        try:
            subprocess.run(
                ["powershell", "-Command", ps_script],
                check=True,
                capture_output=True
            )
            logger.debug(f"{app_name} activated")

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to activate {app_name}: {e}")
            raise RuntimeError(f"Could not activate {app_name}") from e

    def get_running_apps(self) -> List[str]:
        """
        Get list of running applications.

        Returns:
            List of application names
        """
        logger.debug("Getting running applications")

        ps_script = """
        Get-Process | Where-Object {$_.MainWindowTitle -ne ""} | Select-Object -ExpandProperty ProcessName
        """

        try:
            result = subprocess.run(
                ["powershell", "-Command", ps_script],
                check=True,
                capture_output=True,
                text=True
            )

            # Parse line-separated list
            apps = [app.strip() for app in result.stdout.split("\n") if app.strip()]
            logger.debug(f"Found {len(apps)} running apps")
            return apps

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to get running apps: {e}")
            return []

    def get_screen_size(self) -> Tuple[int, int]:
        """
        Get primary screen resolution.

        Returns:
            (width, height) tuple
        """
        if self._has_pywin32:
            try:
                import win32api
                import win32con

                width = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
                height = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)

                logger.debug(f"Screen size: {width}x{height}")
                return (width, height)

            except Exception as e:
                logger.warning(f"Could not get screen size via Win32: {e}")

        # Fallback to PowerShell
        ps_script = """
        Add-Type -AssemblyName System.Windows.Forms
        $screen = [System.Windows.Forms.Screen]::PrimaryScreen
        Write-Output "$($screen.Bounds.Width),$($screen.Bounds.Height)"
        """

        try:
            result = subprocess.run(
                ["powershell", "-Command", ps_script],
                check=True,
                capture_output=True,
                text=True
            )

            width, height = map(int, result.stdout.strip().split(","))
            logger.debug(f"Screen size: {width}x{height}")
            return (width, height)

        except Exception as e:
            logger.warning(f"Could not determine screen size: {e}, using default 1920x1080")
            return (1920, 1080)

    def __repr__(self):
        return "<WindowsDriver (WindowsACI)>"
