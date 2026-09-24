using System.Diagnostics;
using System.Text;

namespace MyWorkDay.Core;

/// <summary>Real Windows Action Center toasts via PowerShell - no extra package dependency.
/// A plain, unregistered app id never actually pops the banner (Windows silently accepts the
/// call and files it into notification history without showing it), so this borrows the AUMID
/// Windows auto-registers for PowerShell's own Start Menu shortcut - guaranteed to exist, at
/// the cost of the toast showing PowerShell's name/icon instead of this app's. Same technique
/// (and the same real, confirmed pitfalls) as the Python build's show_windows_toast.</summary>
public sealed class ToastService
{
    private const string Aumid = @"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe";
    private readonly ErrorLogService _errorLog;

    public ToastService(ErrorLogService errorLog) => _errorLog = errorLog;

    public void Show(string title, string message)
    {
        var safeTitle = PsSingleQuoteEscape(XmlEscape(title));
        var safeMessage = PsSingleQuoteEscape(XmlEscape(message));
        var script =
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null; " +
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null; " +
            $"$t = '<toast><visual><binding template=\"ToastGeneric\"><text>{safeTitle}</text><text>{safeMessage}</text></binding></visual></toast>'; " +
            "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument; " +
            "$xml.LoadXml($t); " +
            "$toast = New-Object Windows.UI.Notifications.ToastNotification $xml; " +
            $"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{Aumid}').Show($toast)";

        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = "powershell",
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            psi.ArgumentList.Add("-NoProfile");
            psi.ArgumentList.Add("-NonInteractive");
            psi.ArgumentList.Add("-Command");
            psi.ArgumentList.Add(script);
            using var proc = Process.Start(psi);
            if (proc is null) return;
            proc.WaitForExit(10_000);
            if (proc.ExitCode != 0)
            {
                var stderr = proc.StandardError.ReadToEnd();
                _errorLog.Log($"ToastService.Show: powershell exited {proc.ExitCode}: {Truncate(stderr, 500)}");
            }
        }
        catch (Exception ex)
        {
            _errorLog.Log("ToastService.Show failed", ex);
        }
    }

    private static string XmlEscape(string s) => s.Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;");

    // Inside a PowerShell single-quoted string, a literal ' must be doubled to '' - an
    // unescaped apostrophe otherwise terminates the string early and corrupts the script.
    private static string PsSingleQuoteEscape(string s) => s.Replace("'", "''");

    private static string Truncate(string s, int len) => s.Length <= len ? s : s[..len];
}
