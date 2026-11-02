using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;

namespace Takumi.Desktop;

// This is Takumi's own native Windows chrome, not a simulated HTML title bar.
// Keep title buttons as accessible WinForms controls, so they work while the
// local Python service is starting, unavailable, or recovering from a crash.
internal sealed class TakumiTitleBar : Panel
{
    internal const int BarHeight = 46;
    internal const int ButtonWidth = 47;
    private readonly Form owner;
    private readonly Label title;
    private readonly PictureBox mark;
    private readonly CaptionButton minimize;
    private readonly CaptionButton maximize;
    private readonly CaptionButton close;
    private Color labelColor;

    internal Rectangle MaximizeBoundsInForm => owner.RectangleToClient(maximize.RectangleToScreen(maximize.ClientRectangle));
    internal int CaptionDragRight => minimize.Left;

    internal TakumiTitleBar(Form owner, string assets)
    {
        this.owner = owner;
        Name = "Takumi title bar";
        Dock = DockStyle.Top;
        Height = BarHeight;
        MinimumSize = new Size(0, BarHeight);
        TabStop = false;
        DoubleBuffered = true;
        labelColor = Color.FromArgb(40, 58, 74);

        // Use the owner's existing transparent derivative without recoloring artwork.
        mark = new PictureBox
        {
            Name = "Takumi icon", Location = new Point(16, 10), Size = new Size(26, 26),
            SizeMode = PictureBoxSizeMode.Zoom, BackColor = Color.Transparent,
            TabStop = false
        };
        foreach (var file in new[] { "../web/mark-clean.png", "takumi-icon.png", "takumi-icon-large.png" })
        {
            var full = Path.Combine(assets, file);
            if (!File.Exists(full)) continue;
            try { mark.Image = Image.FromFile(full); break; }
            catch (Exception) { /* keep the packaged app usable without optional art */ }
        }
        title = new Label
        {
            Name = "Takumi window title", Text = "Takumi", AutoSize = false,
            Location = new Point(52, 9), Size = new Size(160, 30),
            Font = new Font("Georgia", 14, FontStyle.Regular),
            TextAlign = ContentAlignment.MiddleLeft, BackColor = Color.Transparent,
            ForeColor = labelColor, TabStop = false
        };

        minimize = new CaptionButton("Minimize Takumi", CaptionSymbol.Minimize);
        maximize = new CaptionButton("Maximize Takumi", CaptionSymbol.Maximize);
        close = new CaptionButton("Close Takumi", CaptionSymbol.Close);
        Controls.Add(mark);
        Controls.Add(title);
        Controls.Add(minimize);
        Controls.Add(maximize);
        Controls.Add(close);
        minimize.Click += (_, _) => owner.WindowState = FormWindowState.Minimized;
        maximize.Click += (_, _) => ToggleMaximize();
        close.Click += (_, _) => owner.Close();
        Resize += (_, _) => PositionButtons();
        DpiChangedAfterParent += (_, _) => PositionButtons();
        owner.Resize += (_, _) => UpdateMaximizeButton();
        PositionButtons();

        // Releasing the child control capture before requesting the native
        // caption drag preserves Windows edge snap and moving between screens.
        MouseDown += StartDrag;
        title.MouseDown += StartDrag;
        mark.MouseDown += StartDrag;
        MouseDoubleClick += (_, e) => { if (e.Button == MouseButtons.Left) ToggleMaximize(); };
        title.MouseDoubleClick += (_, e) => { if (e.Button == MouseButtons.Left) ToggleMaximize(); };
        mark.MouseDoubleClick += (_, e) => { if (e.Button == MouseButtons.Left) ToggleMaximize(); };
        ApplyTheme("porcelain");
        mark.SizeChanged += (_, _) =>
        {
            if (mark.Region is null) return;
            using var outline = new GraphicsPath();
            outline.AddEllipse(mark.ClientRectangle);
            var previous = mark.Region;
            mark.Region = new Region(outline);
            previous.Dispose();
        };
    }

    private void PositionButtons()
    {
        int width = LogicalToDeviceUnits(ButtonWidth);
        close.Bounds = new Rectangle(Math.Max(0, Width - width), 0, width, Height);
        maximize.Bounds = new Rectangle(Math.Max(0, Width - width * 2), 0, width, Height);
        minimize.Bounds = new Rectangle(Math.Max(0, Width - width * 3), 0, width, Height);
    }

    private void StartDrag(object? sender, MouseEventArgs e)
    {
        if (e.Button != MouseButtons.Left || e.Clicks > 1) return;
        NativeChrome.ReleaseCapture();
        NativeChrome.SendMessage(owner.Handle, NativeChrome.WM_NCLBUTTONDOWN, (nint)NativeChrome.HTCAPTION, nint.Zero);
    }

    internal void ToggleMaximize()
    {
        if (!owner.MaximizeBox) return;
        owner.WindowState = owner.WindowState == FormWindowState.Maximized
            ? FormWindowState.Normal : FormWindowState.Maximized;
        UpdateMaximizeButton();
    }

    private void UpdateMaximizeButton()
    {
        maximize.Symbol = owner.WindowState == FormWindowState.Maximized
            ? CaptionSymbol.Restore : CaptionSymbol.Maximize;
        maximize.AccessibleName = owner.WindowState == FormWindowState.Maximized
            ? "Restore Takumi" : "Maximize Takumi";
        maximize.Invalidate();
    }

    internal void ApplyTheme(string name)
    {
        var dark = name is "sumi" or "midnight" or "yoru" or "kintsugi" or "moss" or "beni" or "shinku" or "tsuki" or "ruri";
        var side = name switch
        {
            "sumi" => "#181D21", "midnight" => "#121722", "yoru" => "#131C29",
            "kintsugi" => "#1B2529", "moss" => "#26382F", "sakura" => "#F6ECEE",
            "sage" => "#D8E2D3", "matcha" => "#EAF0E4", "indigo" => "#EAEAF3", "ume" => "#F1E1E5",
            "washi" => "#F3EEE4", "kyoto" => "#EDE4D7", "sora" => "#D9EAF0",
            "akane" => "#F3DFD4", "zen" => "#E8E6DF", "beni" => "#24171C",
            "shinku" => "#1D1517", "shu" => "#F4E1DA", "botan" => "#EEE1ED",
            "tsuki" => "#181C22", "ruri" => "#152238", _ => "#F1F0EC"
        };
        BackColor = ColorTranslator.FromHtml(side);
        mark.BackColor = dark ? ColorTranslator.FromHtml("#FAF7ED") : Color.Transparent;
        using (var outline = new GraphicsPath())
        {
            outline.AddEllipse(mark.ClientRectangle);
            var previous = mark.Region;
            mark.Region = dark ? new Region(outline) : null;
            previous?.Dispose();
        }
        labelColor = dark ? Color.FromArgb(239, 241, 240) : Color.FromArgb(40, 58, 74);
        title.ForeColor = labelColor;
        foreach (var button in new[] { minimize, maximize, close })
            button.SetPalette(BackColor, labelColor);
        Invalidate();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing) mark.Image?.Dispose();
        base.Dispose(disposing);
    }
}

internal enum CaptionSymbol { Minimize, Maximize, Restore, Close }

internal sealed class CaptionButton : Control
{
    private bool hovered;
    private bool pressed;
    private Color background = Color.FromArgb(241, 240, 236);
    private Color foreground = Color.FromArgb(40, 58, 74);
    // Runtime-only glyph state: not a Windows Forms designer property.
    internal CaptionSymbol Symbol;

    internal CaptionButton(string accessibleName, CaptionSymbol symbol)
    {
        Symbol = symbol;
        Name = accessibleName;
        AccessibleName = accessibleName;
        AccessibleRole = AccessibleRole.PushButton;
        TabStop = true;
        SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint |
                 ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
    }

    internal void SetPalette(Color bg, Color fg) { background = bg; foreground = fg; Invalidate(); }
    protected override void OnMouseEnter(EventArgs e) { hovered = true; Invalidate(); base.OnMouseEnter(e); }
    protected override void OnMouseLeave(EventArgs e) { hovered = false; pressed = false; Invalidate(); base.OnMouseLeave(e); }
    protected override void OnMouseDown(MouseEventArgs e) { if (e.Button == MouseButtons.Left) pressed = true; Invalidate(); base.OnMouseDown(e); }
    protected override void OnMouseUp(MouseEventArgs e) { pressed = false; Invalidate(); base.OnMouseUp(e); }
    protected override void OnGotFocus(EventArgs e) { Invalidate(); base.OnGotFocus(e); }
    protected override void OnLostFocus(EventArgs e) { Invalidate(); base.OnLostFocus(e); }
    protected override void OnKeyDown(KeyEventArgs e)
    {
        if (e.KeyCode is Keys.Enter or Keys.Space) { OnClick(EventArgs.Empty); e.Handled = true; }
        base.OnKeyDown(e);
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        var g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        var surface = hovered ? (Symbol == CaptionSymbol.Close ? Color.FromArgb(190, 63, 65)
            : Color.FromArgb(34, foreground)) : background;
        if (pressed) surface = Symbol == CaptionSymbol.Close ? Color.FromArgb(157, 42, 44)
            : Color.FromArgb(55, foreground);
        using (var brush = new SolidBrush(surface)) g.FillRectangle(brush, ClientRectangle);
        using var pen = new Pen((hovered || pressed) && Symbol == CaptionSymbol.Close ? Color.White : foreground, 1.35f);
        var x = Width / 2f;
        var y = Height / 2f;
        switch (Symbol)
        {
            case CaptionSymbol.Minimize: g.DrawLine(pen, x - 5, y + 3, x + 5, y + 3); break;
            case CaptionSymbol.Maximize: g.DrawRectangle(pen, x - 5, y - 5, 10, 10); break;
            case CaptionSymbol.Restore:
                g.DrawRectangle(pen, x - 4, y - 3, 9, 9);
                g.DrawLine(pen, x - 2, y - 5, x + 6, y - 5);
                g.DrawLine(pen, x + 6, y - 5, x + 6, y + 3);
                break;
            case CaptionSymbol.Close:
                g.DrawLine(pen, x - 5, y - 5, x + 5, y + 5);
                g.DrawLine(pen, x + 5, y - 5, x - 5, y + 5);
                break;
        }
        if (Focused && ShowFocusCues)
        {
            var box = new Rectangle(3, 3, Width - 6, Height - 6);
            ControlPaint.DrawFocusRectangle(g, box, foreground, surface);
        }
    }
}

internal static class NativeChrome
{
    internal const int WM_NCHITTEST = 0x0084;
    internal const int WM_NCCALCSIZE = 0x0083;
    internal const int WM_NCACTIVATE = 0x0086;
    internal const int WM_GETMINMAXINFO = 0x0024;
    internal const int WM_NCLBUTTONDOWN = 0x00A1;
    internal const int WM_NCLBUTTONUP = 0x00A2;
    internal const int HTCAPTION = 2;
    internal const int HTLEFT = 10;
    internal const int HTRIGHT = 11;
    internal const int HTTOP = 12;
    internal const int HTTOPLEFT = 13;
    internal const int HTTOPRIGHT = 14;
    internal const int HTBOTTOM = 15;
    internal const int HTBOTTOMLEFT = 16;
    internal const int HTBOTTOMRIGHT = 17;
    internal const int HTMAXBUTTON = 9;
    internal const int WS_THICKFRAME = 0x00040000;
    internal const int WS_CAPTION = 0x00C00000;
    internal const int WS_MINIMIZEBOX = 0x00020000;
    internal const int WS_MAXIMIZEBOX = 0x00010000;
    internal const int WS_SYSMENU = 0x00080000;

    [DllImport("user32.dll")] internal static extern bool ReleaseCapture();
    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    internal static extern nint SendMessage(nint handle, int message, nint wParam, nint lParam);
    [StructLayout(LayoutKind.Sequential)] internal struct Point { internal int X, Y; }
    [StructLayout(LayoutKind.Sequential)] internal struct MinMaxInfo
    {
        internal Point Reserved;
        internal Point MaxSize;
        internal Point MaxPosition;
        internal Point MinTrackSize;
        internal Point MaxTrackSize;
    }
    internal static System.Drawing.Point ScreenPoint(nint parameter)
    {
        // Signed 16-bit screen coordinates are important for monitors to the
        // left of / above the primary display.
        var v = parameter.ToInt64();
        return new System.Drawing.Point(unchecked((short)(v & 0xffff)), unchecked((short)((v >> 16) & 0xffff)));
    }
}
