using System.Diagnostics;
using System.Drawing;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace Takumi.Desktop;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        ApplicationConfiguration.Initialize();
        Application.ThreadException += (_, error) => LaunchDiagnostics.Write("UI exception: " + error.Exception);
        AppDomain.CurrentDomain.UnhandledException += (_, error) => LaunchDiagnostics.Write("Unhandled exception: " + error.ExceptionObject);
        try
        {
            LaunchDiagnostics.Write("Takumi.exe started; version 1.6.0 with Sumi 1.1 and Rei 1.1.");
            Application.Run(new TakumiWindow());
        }
        catch (Exception error)
        {
            LaunchDiagnostics.Write("Fatal application startup error: " + error);
            MessageBox.Show("Takumi could not start.\n\n" + error.Message +
                "\n\nDiagnostics: " + LaunchDiagnostics.LogPath, "Takumi - Startup error",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}

internal static class LaunchDiagnostics
{
    private static readonly object Sync = new();
    internal static readonly string LogPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "Takumi", "Logs", "desktop-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".log");
    internal static void Write(string message)
    {
        try
        {
            lock (Sync)
            {
                Directory.CreateDirectory(Path.GetDirectoryName(LogPath)!);
                File.AppendAllText(LogPath, DateTimeOffset.Now.ToString("O") + "  " + message + Environment.NewLine);
            }
        }
        catch (Exception) { /* A logging failure must never crash the UI. */ }
    }
}

internal sealed class TakumiWindow : Form
{
    private readonly WebView2 web = new();
    private readonly Panel contentHost = new();
    private readonly TakumiTitleBar chrome;
    private Process? backend;
    private int port;
    private bool createdBackend;
    private readonly string root = AppContext.BaseDirectory;
    private static readonly HttpClient health = new() { Timeout = TimeSpan.FromMilliseconds(650) };

    internal TakumiWindow()
    {
        Text = "Takumi";
        Width = 1400;
        Height = 900;
        MinimumSize = new Size(750, 540);
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(251, 250, 248);
        // Takumi owns its caption surface. Native hit testing keeps resize, drag,
        // Win+Z and Windows snap behavior instead of a blue Windows caption.
        FormBorderStyle = FormBorderStyle.None;
        MaximizeBox = true; MinimizeBox = true;
        SetStyle(ControlStyles.ResizeRedraw, true);
        var icon = Path.Combine(root, "assets", "takumi.ico");
        if (File.Exists(icon))
            try { Icon = new Icon(icon); } catch (Exception) { /* use app icon */ }
        chrome = new TakumiTitleBar(this, Path.Combine(root, "assets"));
        contentHost.Dock = DockStyle.Fill;
        contentHost.BackColor = BackColor;
        web.Dock = DockStyle.Fill;
        web.DefaultBackgroundColor = Color.FromArgb(251, 250, 248);
        contentHost.Controls.Add(web);
        Controls.Add(contentHost);
        Controls.Add(chrome);
        Shown += async (_, _) => await InitializeAsync();
        LaunchDiagnostics.Write("Window created. Application folder: " + root);
        FormClosed += (_, _) => StopOwnedBackend();
    }

    // Preserve system window affordances even with a fully custom title bar.
    protected override CreateParams CreateParams
    {
        get
        {
            var parameters = base.CreateParams;
            parameters.Style &= ~NativeChrome.WS_CAPTION;
            parameters.Style |= NativeChrome.WS_THICKFRAME | NativeChrome.WS_SYSMENU |
                NativeChrome.WS_MINIMIZEBOX | NativeChrome.WS_MAXIMIZEBOX;
            return parameters;
        }
    }

    protected override void WndProc(ref Message m)
    {
        if (m.Msg == NativeChrome.WM_NCCALCSIZE)
        {
            m.Result = nint.Zero; // no default painted caption
            return;
        }
        if (m.Msg == NativeChrome.WM_NCACTIVATE && WindowState != FormWindowState.Minimized)
        {
            // Preserve activation bookkeeping without asking Windows to paint
            // a classic caption over our client-owned title bar.
            m.LParam = (nint)(-1);
            base.WndProc(ref m);
            chrome?.Invalidate(true);
            return;
        }
        if (m.Msg == NativeChrome.WM_GETMINMAXINFO && m.LParam != nint.Zero && IsHandleCreated)
        {
            // Respect each monitor's working area, including the taskbar.
            var monitor = Screen.FromHandle(Handle);
            var info = Marshal.PtrToStructure<NativeChrome.MinMaxInfo>(m.LParam);
            info.MaxPosition.X = monitor.WorkingArea.Left - monitor.Bounds.Left;
            info.MaxPosition.Y = monitor.WorkingArea.Top - monitor.Bounds.Top;
            info.MaxSize.X = monitor.WorkingArea.Width;
            info.MaxSize.Y = monitor.WorkingArea.Height;
            Marshal.StructureToPtr(info, m.LParam, false);
            m.Result = nint.Zero;
            return;
        }
        if (m.Msg == NativeChrome.WM_NCHITTEST && WindowState == FormWindowState.Normal)
        {
            var p = PointToClient(NativeChrome.ScreenPoint(m.LParam));
            int edge = LogicalToDeviceUnits(7);
            bool left = p.X >= 0 && p.X < edge, right = p.X >= Width - edge && p.X < Width;
            bool top = p.Y >= 0 && p.Y < edge, bottom = p.Y >= Height - edge && p.Y < Height;
            int hit = (left, right, top, bottom) switch
            {
                (true, _, true, _) => NativeChrome.HTTOPLEFT,
                (_, true, true, _) => NativeChrome.HTTOPRIGHT,
                (true, _, _, true) => NativeChrome.HTBOTTOMLEFT,
                (_, true, _, true) => NativeChrome.HTBOTTOMRIGHT,
                (true, _, _, _) => NativeChrome.HTLEFT,
                (_, true, _, _) => NativeChrome.HTRIGHT,
                (_, _, true, _) => NativeChrome.HTTOP,
                (_, _, _, true) => NativeChrome.HTBOTTOM,
                _ => 0
            };
            if (hit != 0) { m.Result = (nint)hit; return; }
        }
        if (m.Msg == NativeChrome.WM_NCHITTEST)
        {
            var p = PointToClient(NativeChrome.ScreenPoint(m.LParam));
            if (chrome.MaximizeBoundsInForm.Contains(p))
            {
                // Windows 11 Snap Layout hover recognizes HTMAXBUTTON.
                m.Result = (nint)NativeChrome.HTMAXBUTTON;
                return;
            }
            if (p.Y >= 0 && p.Y < chrome.Height &&
                p.X >= 0 && p.X < chrome.CaptionDragRight)
            {
                m.Result = (nint)NativeChrome.HTCAPTION;
                return;
            }
        }
        if (m.Msg == NativeChrome.WM_NCLBUTTONDOWN && m.WParam == (nint)NativeChrome.HTMAXBUTTON)
        {
            m.Result = nint.Zero;
            return;
        }
        if (m.Msg == NativeChrome.WM_NCLBUTTONUP && m.WParam == (nint)NativeChrome.HTMAXBUTTON)
        {
            chrome.ToggleMaximize();
            m.Result = nint.Zero;
            return;
        }
        base.WndProc(ref m);
    }

    // Never attach a newly installed window to an outdated backend that serves stale UI files.
    private static string BuildStamp(string applicationRoot)
    {
        using var digest = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        var files = new[] { "takumi", "web", "models", "expertise", "assets" }
            .SelectMany(folder => Directory.EnumerateFiles(Path.Combine(applicationRoot, folder), "*", SearchOption.AllDirectories))
            .Where(file => !file.Split(Path.DirectorySeparatorChar).Contains("__pycache__") &&
                           Path.GetExtension(file) is not ".pyc" and not ".pyo")
            .Select(file => Path.GetRelativePath(applicationRoot, file).Replace('\\', '/'))
            .OrderBy(file => file, StringComparer.Ordinal);
        foreach (var relative in files)
        {
            var file = Path.Combine(applicationRoot, relative.Replace('/', Path.DirectorySeparatorChar));
            if (!File.Exists(file)) throw new FileNotFoundException("Missing Takumi source: " + relative);
            digest.AppendData(Encoding.UTF8.GetBytes(relative + "\0"));
            digest.AppendData(File.ReadAllBytes(file));
            digest.AppendData(new byte[] { 0 });
        }
        return Convert.ToHexString(digest.GetHashAndReset()).ToLowerInvariant();
    }

    private async Task<bool> Ready(int target)
    {
        try
        {
            using var response = await health.GetAsync($"http://127.0.0.1:{target}/api/runtime-identity");
            if (!response.IsSuccessStatusCode) return false;
            using var document = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
            var identity = document.RootElement;
            var version = identity.GetProperty("version").GetString();
            var stamp = identity.GetProperty("build_stamp").GetString();
            var diskStamp = identity.GetProperty("disk_build_stamp").GetString();
            var sourceChanged = identity.GetProperty("source_changed_since_start").GetBoolean();
            var dataRoot = identity.GetProperty("data_root").GetString();
            var expectedData = Environment.GetEnvironmentVariable("TAKUMI_DATA_DIR") ??
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".takumi");
            var matches = version == "1.6.0" && stamp == BuildStamp(root) &&
                diskStamp == stamp && !sourceChanged &&
                string.Equals(Path.GetFullPath(dataRoot ?? ""), Path.GetFullPath(expectedData), StringComparison.OrdinalIgnoreCase);
            if (!matches) LaunchDiagnostics.Write("Different Takumi backend on port " + target + "; not reusing it.");
            return matches;
        }
        catch (Exception) { return false; }
    }

    private static int AvailablePort()
    {
        var socket = new TcpListener(IPAddress.Loopback, 0);
        socket.Start();
        var port = ((IPEndPoint)socket.LocalEndpoint).Port;
        socket.Stop();
        return port;
    }

    private static string? FindPython()
    {
        var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        var programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
        foreach (string binary in new[] {
            "py.exe", "python.exe", "python3.exe",
            Path.Combine(local, "Programs", "Python", "Python313", "python.exe"),
            Path.Combine(local, "Programs", "Python", "Python312", "python.exe"),
            Path.Combine(programFiles, "Python313", "python.exe") })
        {
            try
            {
                var start = new ProcessStartInfo(binary)
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true
                };
                if (binary == "py.exe") start.ArgumentList.Add("-3");
                start.ArgumentList.Add("-c");
                start.ArgumentList.Add("import sys; print(sys.version_info.major, sys.version_info.minor)");
                using var process = Process.Start(start);
                if (process is null || !process.WaitForExit(4000) || process.ExitCode != 0) continue;
                var version = process.StandardOutput.ReadToEnd().Trim().Split(' ');
                if (version.Length == 2 && int.TryParse(version[0], out int major) &&
                    int.TryParse(version[1], out int minor) && (major > 3 || major == 3 && minor >= 11))
                    return binary;
            }
            catch (Exception) { /* check next Python launcher */ }
        }
        return null;
    }

    private async Task InitializeAsync()
    {
        try
        {
            if (!Directory.Exists(Path.Combine(root, "takumi")) ||
                !File.Exists(Path.Combine(root, "web", "index.html")))
                throw new FileNotFoundException("Takumi's application files are missing next to the EXE. Please extract the entire release ZIP.");

            // Reuse a user-enabled background session so Discord supervision and
            // iPhone connectivity continue even when the desktop window closes.
            if (await Ready(8765))
            {
                port = 8765;
                LaunchDiagnostics.Write("Using verified same-build Takumi service on port 8765.");
            }
            else
            {
                var python = FindPython();
                if (python is null)
                    throw new FileNotFoundException("Python 3.11 or newer is required for the local Takumi service. Run REPAIR-Takumi.cmd from the extracted Windows Setup package, then reopen Takumi.");
                port = AvailablePort();
                LaunchDiagnostics.Write("Starting Python service with " + python + " on local port " + port);
                var start = new ProcessStartInfo(python)
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    WorkingDirectory = root
                };
                if (python == "py.exe") start.ArgumentList.Add("-3");
                start.ArgumentList.Add("-m");
                start.ArgumentList.Add("takumi");
                start.ArgumentList.Add("--no-browser");
                start.ArgumentList.Add("--port");
                start.ArgumentList.Add(port.ToString());
                start.Environment["PYTHONPATH"] = root;
                start.Environment["PYTHONUTF8"] = "1";
                start.Environment["PYTHONUNBUFFERED"] = "1";
                backend = Process.Start(start) ?? throw new InvalidOperationException("Could not start Takumi's local service.");
                backend.OutputDataReceived += (_, line) => { if (!string.IsNullOrEmpty(line.Data)) LaunchDiagnostics.Write("Service: " + line.Data); };
                backend.ErrorDataReceived += (_, line) => { if (!string.IsNullOrEmpty(line.Data)) LaunchDiagnostics.Write("Service error: " + line.Data); };
                backend.BeginOutputReadLine();
                backend.BeginErrorReadLine();
                LaunchDiagnostics.Write("Service process started, PID " + backend.Id);
                createdBackend = true;
                bool launched = false;
                for (var i = 0; i < 75; i++)
                {
                    if (await Ready(port)) { launched = true; break; }
                    if (backend.HasExited) break;
                    await Task.Delay(200);
                }
                if (!launched)
                    throw new InvalidOperationException("Takumi's local service did not start. " +
                        (backend.HasExited ? "Python exited with code " + backend.ExitCode + ". " : "Python is not responding. ") +
                        "See the desktop log for the Python error: " + LaunchDiagnostics.LogPath);
            }
            // User-specific WebView2 profile: stable sessions, extensions disabled,
            // no shared browser tabs. The Evergreen runtime is installed by Windows.
            var profile = Environment.GetEnvironmentVariable("TAKUMI_WEBVIEW_PROFILE") ??
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Takumi", "WebView2");
            LaunchDiagnostics.Write("Initializing WebView2 in " + profile);
            var environment = await CoreWebView2Environment.CreateAsync(null, profile);
            await web.EnsureCoreWebView2Async(environment);
            if (web.CoreWebView2 is null) throw new InvalidOperationException("Embedded browser failed to initialize.");
            web.CoreWebView2.WebMessageReceived += (_, e) =>
            {
                // Only the current local Takumi page can adjust native chrome.
                if (!Uri.TryCreate(e.Source, UriKind.Absolute, out var from) ||
                    from.Scheme != "http" || from.Host != "127.0.0.1" || from.Port != port) return;
                try
                {
                    using var json = JsonDocument.Parse(e.TryGetWebMessageAsString());
                    var value = json.RootElement;
                    if (value.TryGetProperty("type", out var type) && type.GetString() == "takumi.theme" &&
                        value.TryGetProperty("theme", out var theme) && theme.ValueKind == JsonValueKind.String)
                        chrome.ApplyTheme(theme.GetString() ?? "porcelain");
                }
                catch (Exception) { /* ignore untrusted or malformed messages */ }
            };
            web.CoreWebView2.NavigationCompleted += async (_, _) =>
            {
                try { await web.CoreWebView2.ExecuteScriptAsync(
                    "window.chrome?.webview?.postMessage(JSON.stringify({type:'takumi.theme',theme:document.body.dataset.theme||'porcelain'}))"); }
                catch (Exception) { /* theme is optional */ }
            };
            web.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
            web.CoreWebView2.Settings.AreDevToolsEnabled =
                Environment.GetEnvironmentVariable("TAKUMI_DEVTOOLS") == "1";
            web.CoreWebView2.Settings.IsStatusBarEnabled = false;
            web.CoreWebView2.Settings.IsPasswordAutosaveEnabled = false;
            web.CoreWebView2.Settings.IsGeneralAutofillEnabled = false;
            web.CoreWebView2.NavigationStarting += (_, evt) =>
            {
                if (!Uri.TryCreate(evt.Uri, UriKind.Absolute, out var url)) { evt.Cancel = true; return; }
                if (url.Host == "127.0.0.1" && url.Port == port && url.Scheme == "http") return;
                evt.Cancel = true;
                if (url.Scheme is "https" or "http")
                    try { Process.Start(new ProcessStartInfo(url.ToString()) { UseShellExecute = true }); }
                    catch (Exception) { /* no default browser configured */ }
            };
            web.CoreWebView2.NewWindowRequested += (_, evt) =>
            {
                evt.Handled = true;
                if (Uri.TryCreate(evt.Uri, UriKind.Absolute, out var url) && url.Scheme == "https")
                    try { Process.Start(new ProcessStartInfo(url.ToString()) { UseShellExecute = true }); }
                    catch (Exception) { }
            };
            web.Source = new Uri($"http://127.0.0.1:{port}/?build={BuildStamp(root)[..12]}");
            LaunchDiagnostics.Write("Takumi desktop ready; URL is a private loopback endpoint.");
        }
        catch (Exception error)
        {
            LaunchDiagnostics.Write("Startup failed: " + error);
            ShowStartupError(error);
        }
    }

    private void ShowStartupError(Exception error)
    {
        web.Visible = false;
        BackColor = Color.FromArgb(251, 250, 248);
        var panel = new Panel { Dock = DockStyle.Fill, BackColor = BackColor, Padding = new Padding(38) };
        var title = new Label
        {
            Text = "Takumi needs attention",
            Font = new Font("Segoe UI", 24, FontStyle.Regular),
            ForeColor = Color.FromArgb(40, 55, 49),
            Location = new Point(38, 60),
            AutoSize = true
        };
        var detail = new TextBox
        {
            Text = "The app could not start. Your files have not been deleted.\r\n\r\n" +
                   error.Message + "\r\n\r\nDiagnostic log: " + LaunchDiagnostics.LogPath,
            Font = new Font("Segoe UI", 11),
            ForeColor = Color.FromArgb(55, 65, 58),
            BackColor = BackColor,
            Location = new Point(39, 132),
            Size = new Size(730, 190),
            BorderStyle = BorderStyle.None,
            Multiline = true,
            ReadOnly = true,
            TabStop = false
        };
        var open = new Button
        {
            Text = "Open diagnostics folder",
            Font = new Font("Segoe UI", 10, FontStyle.Regular),
            Location = new Point(38, 340),
            Size = new Size(225, 40)
        };
        open.Click += (_, _) =>
        {
            try { Process.Start(new ProcessStartInfo(Path.GetDirectoryName(LaunchDiagnostics.LogPath)!) { UseShellExecute = true }); }
            catch (Exception) { }
        };
        panel.Controls.Add(title);
        panel.Controls.Add(detail);
        panel.Controls.Add(open);
        contentHost.Controls.Add(panel);
        panel.BringToFront();
    }

    private void StopOwnedBackend()
    {
        if (!createdBackend || backend is null) return;
        try
        {
            if (!backend.HasExited) backend.Kill(entireProcessTree: true);
        }
        catch (Exception) { /* process exited naturally */ }
        finally { backend.Dispose(); }
    }
}
