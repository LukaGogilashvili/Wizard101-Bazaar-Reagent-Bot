using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

[assembly: AssemblyTitle("Wizard101 Bazaar Buyer")]
[assembly: AssemblyVersion("1.1.0.0")]

sealed class BazaarBuyer : Form
{
    readonly TextBox reagent = new TextBox();
    readonly RadioButton buy = new RadioButton();
    readonly RadioButton scan = new RadioButton();
    readonly Button start = new Button();
    readonly Button stop = new Button();
    readonly TextBox activity = new TextBox();
    readonly Label state = new Label();
    readonly string temp = Path.Combine(Path.GetTempPath(), "BazaarBuyer-" + Guid.NewGuid().ToString("N"));
    Process worker;
    bool unpacked, running, stopping, closing;

    [STAThread]
    static void Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        using (var app = new BazaarBuyer()) Application.Run(app);
    }

    BazaarBuyer()
    {
        Text = "Wizard101 Bazaar Buyer";
        Font = new Font("Segoe UI", 10);
        BackColor = Color.White;
        ForeColor = Color.FromArgb(30, 40, 50);
        AutoScaleDimensions = new SizeF(96, 96);
        AutoScaleMode = AutoScaleMode.Dpi;
        ClientSize = new Size(548, 480);
        MinimumSize = Size;
        StartPosition = FormStartPosition.CenterScreen;
        var layout = new TableLayoutPanel {
            Dock = DockStyle.Fill, Padding = new Padding(22, 18, 22, 18), ColumnCount = 1, RowCount = 9
        };
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        for (int i = 0; i < 8; i++) layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        Controls.Add(layout);
        AddLabel(layout, "Bazaar Reagent Buyer", 18, FontStyle.Bold);
        AddLabel(layout, "Reagent name", 10, FontStyle.Bold);
        reagent.Name = "ReagentName";
        reagent.AccessibleName = "Reagent name";
        reagent.MaxLength = 64;
        reagent.Text = "Outsider Relic";
        reagent.Dock = DockStyle.Top;
        layout.Controls.Add(reagent);

        var modes = new FlowLayoutPanel {AutoSize = true, Dock = DockStyle.Top, WrapContents = false};
        buy.Text = "Buy continuously";
        scan.Text = "Scan once";
        buy.AutoSize = scan.AutoSize = true;
        buy.Checked = true;
        modes.Controls.AddRange(new Control[] {buy, scan});
        layout.Controls.Add(modes);

        Label modeHint = AddLabel(layout, "", 10, FontStyle.Regular);
        Action explain = () => modeHint.Text = buy.Checked
            ? "Buys matching stock at the displayed gold price and watches for restocks."
            : "Checks the selected category once. No gold is spent.";
        buy.CheckedChanged += delegate { explain(); };
        explain();
        AddLabel(layout, "After starting, switch to Wizard101. Hover over your reagent category and press F8. Press F10 in-game to stop.", 10, FontStyle.Regular);

        var actions = new FlowLayoutPanel {AutoSize = true, Dock = DockStyle.Top};
        start.Text = "Start";
        start.BackColor = Color.FromArgb(32, 86, 124);
        start.ForeColor = Color.White;
        start.FlatStyle = FlatStyle.Flat;
        stop.Text = "Stop";
        stop.Enabled = false;
        foreach (Button b in new[] {start, stop}) {
            b.Size = new Size(98, 34);
            actions.Controls.Add(b);
        }
        start.Click += async delegate { await RunSession(); };
        stop.Click += delegate { StopSession(); };
        AcceptButton = start;
        layout.Controls.Add(actions);
        state.Text = "Ready";
        state.AutoSize = true;
        state.Font = new Font(Font, FontStyle.Bold);
        state.Margin = new Padding(3, 12, 3, 4);
        layout.Controls.Add(state);
        activity.Multiline = activity.ReadOnly = true;
        activity.ScrollBars = ScrollBars.Vertical;
        activity.Dock = DockStyle.Fill;
        activity.BackColor = Color.FromArgb(246, 248, 250);
        activity.AccessibleName = "Session log";
        layout.Controls.Add(activity);
        FormClosing += delegate(object sender, FormClosingEventArgs e) {
            if (running) {
                e.Cancel = true;
                closing = true;
                StopSession();
            }
        };
        FormClosed += delegate { CleanTemp(); };
    }

    static Label AddLabel(TableLayoutPanel parent, string text, int size, FontStyle style)
    {
        var label = new Label {
            Text = text, AutoSize = true, Dock = DockStyle.Fill,
            Font = new Font("Segoe UI", size, style), Margin = new Padding(3, 5, 3, 5)
        };
        parent.Controls.Add(label);
        return label;
    }

    void Log(string message)
    {
        if (activity.TextLength > 32000)
            activity.Text = activity.Text.Substring(activity.TextLength / 2);
        activity.AppendText(message + Environment.NewLine);
    }

    void CleanTemp()
    {
        try { if (Directory.Exists(temp)) Directory.Delete(temp, true); }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    void Unpack()
    {
        if (unpacked) return;
        CleanTemp();
        Directory.CreateDirectory(temp);
        using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("buyer.zip"))
        using (var zip = new ZipArchive(stream, ZipArchiveMode.Read))
            zip.ExtractToDirectory(temp);
        unpacked = true;
    }

    void StopSession()
    {
        stopping = true;
        state.Text = "Stopping";
        stop.Enabled = false;
        if (worker != null) worker.StandardInput.Close();
    }

    async Task ReadLog(StreamReader source)
    {
        string line;
        while ((line = await source.ReadLineAsync()) != null) {
            Log(line);
            if (stopping) continue;
            if (line.StartsWith("Waiting for F8")) state.Text = "Waiting for F8";
            if (line.StartsWith("Watching the chosen")) state.Text = "Watching the Bazaar";
        }
    }

    async Task RunSession()
    {
        if (running) return;
        string name = reagent.Text.Trim();
        if (name.Length == 0) {
            state.Text = "Enter a reagent name";
            reagent.Focus();
            return;
        }
        running = true;
        stopping = false;
        start.Enabled = buy.Enabled = scan.Enabled = reagent.Enabled = false;
        stop.Enabled = true;
        state.Text = "Starting";
        activity.Clear();
        Log((scan.Checked ? "Scan once" : "Continuous buying") + " · " + name);
        try {
            await Task.Run((Action)Unpack);
            if (stopping) return;
            string app = Path.Combine(temp, "Wizard101 Bazaar Buyer");
            var info = new ProcessStartInfo {
                FileName = Path.Combine(app, "runtime", "python.exe"), WorkingDirectory = app,
                Arguments = "-B -u -X utf8 bazaar_buyer.py --gui-worker " + (scan.Checked ? "scan" : "buy"),
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            info.EnvironmentVariables["WIZARD101_BUYER_REAGENT"] = name;
            worker = Process.Start(info);
            await Task.WhenAll(ReadLog(worker.StandardOutput), ReadLog(worker.StandardError));
            await Task.Run((Action)worker.WaitForExit);
            if (worker.ExitCode != 0) Log("Buyer exited with code " + worker.ExitCode + ".");
        }
        catch (Exception ex) { Log(ex.Message); }
        finally {
            if (worker != null) { worker.Dispose(); worker = null; }
            running = false;
            start.Enabled = buy.Enabled = scan.Enabled = reagent.Enabled = true;
            stop.Enabled = false;
            state.Text = "Session ended";
            if (closing) Close();
        }
    }
}
