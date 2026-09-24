namespace MyWorkDay.Core;

/// <summary>Opens a URL in the user's default browser - shared by every window that has a
/// clickable ticket key (My Work, Team Worklog, Needs Logging, ticket search results).</summary>
public static class UrlOpener
{
    public static void Open(string? url)
    {
        if (string.IsNullOrWhiteSpace(url)) return;
        try { System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(url) { UseShellExecute = true }); }
        catch { }
    }
}
