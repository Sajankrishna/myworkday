namespace MyWorkDay.Core;

/// <summary>The same "work day" bucketing rule the Python build used: Eastern time, and
/// anything at/after 10pm rolls into the next day's bucket (so a late-night worklog entry
/// lands on the day you'd actually call it, not technically-already-tomorrow).</summary>
public static class WorkDate
{
    private const int CutoffHour = 22;

    public static readonly TimeZoneInfo Eastern = ResolveEastern();

    private static TimeZoneInfo ResolveEastern()
    {
        // Windows and IANA use different ids for the same zone - try both so this runs the
        // same whether .NET resolves via the OS (Windows id) or ICU (IANA id).
        foreach (var id in new[] { "Eastern Standard Time", "America/New_York" })
        {
            try { return TimeZoneInfo.FindSystemTimeZoneById(id); }
            catch (TimeZoneNotFoundException) { }
        }
        return TimeZoneInfo.Local;
    }

    public static string KeyFor(DateTimeOffset ts)
    {
        var local = TimeZoneInfo.ConvertTime(ts, Eastern);
        if (local.Hour >= CutoffHour) local = local.AddDays(1);
        return local.ToString("yyyy-MM-dd");
    }

    public static string Today() => KeyFor(DateTimeOffset.UtcNow);

    public static DateTime Parse(string dateKey) => DateTime.ParseExact(dateKey, "yyyy-MM-dd", null);
}
