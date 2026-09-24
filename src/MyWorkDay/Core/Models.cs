namespace MyWorkDay.Core;

/// <summary>One real, timestamped thing that happened on a ticket - a worklog entry you logged,
/// or a comment you wrote. Never a git commit: this build is Jira-only (see README).</summary>
public sealed class TicketAction
{
    public DateTimeOffset Timestamp { get; init; }
    public string Kind { get; init; } = ""; // "worklog" | "comment"
    public string Message { get; init; } = "";
    public int Minutes { get; init; }

    public string TimeLabel => Timestamp.ToLocalTime().ToString("h:mm tt").TrimStart('0');
    public string KindLabel => Kind switch
    {
        "worklog" => "Logged time",
        "comment" => "Commented",
        _ => Kind,
    };
}

/// <summary>One ticket's real Jira activity on the selected work-day: worklog entries you logged
/// that day plus comments you wrote that day. LoggedMinutes is the actual sum of your own worklog
/// entries dated this day - never an estimate.</summary>
public sealed class TicketDayRow
{
    public string Key { get; init; } = "";
    public string Summary { get; init; } = "";
    public string? Status { get; init; }
    public string? StatusCategory { get; init; } // "new" | "indeterminate" | "done"
    public int LoggedMinutes { get; init; }
    // Jira's own "Original Estimate" (timetracking.originalEstimateSeconds) - a real, manually
    // set field, when Jira has one for this ticket. Null (not 0) means nobody estimated it.
    public int? ExpectedMinutes { get; init; }
    public List<TicketAction> Actions { get; init; } = new();
    public string? JiraUrl { get; init; }

    public bool HasWorklog => Actions.Any(a => a.Kind == "worklog");
    public bool HasComment => Actions.Any(a => a.Kind == "comment");
    public string ActivityKind => HasWorklog ? "Logged time" : (HasComment ? "Commented only" : "Assigned to you");
    public DateTimeOffset LastActivity => Actions.Count > 0 ? Actions.Max(a => a.Timestamp) : DateTimeOffset.MinValue;
}

/// <summary>A ticket assigned to you (right now, not day-scoped) with no time logged on the
/// selected day yet - the Jira-only replacement for the old git-based "needs logging" reminder.
/// Real Jira assignment + real Jira status, nothing inferred.</summary>
public sealed class OpenTicketRow
{
    public string Key { get; init; } = "";
    public string Summary { get; init; } = "";
    public string? Status { get; init; }
    public string? StatusCategory { get; init; }
    public string? JiraUrl { get; init; }
    // Jira's own "Original Estimate", when set on the ticket - null means unset, not zero.
    public int? ExpectedMinutes { get; init; }
}

public sealed class DonutSlice
{
    public string Label { get; init; } = "";
    public int Minutes { get; init; }
    public int Pct { get; init; }
}

public sealed class DashboardStats
{
    public int LoggedMinutes { get; init; }
    public string Logged => FormatMinutes(LoggedMinutes);
    public int DailyTargetMinutes { get; init; }
    public int TargetPct { get; init; }
    public int TicketsTouched { get; init; }
    public int CommentsMade { get; init; }
    public int OpenAssigned { get; init; }
    public int WeekLoggedMinutes { get; init; }
    public string WeekLogged => FormatMinutes(WeekLoggedMinutes);
    public int WeekTickets { get; init; }

    public static string FormatMinutes(int mins)
    {
        var h = mins / 60;
        var m = mins % 60;
        if (h > 0 && m > 0) return $"{h}h {m}m";
        if (h > 0) return $"{h}h";
        return $"{m}m";
    }
}

public sealed class CalendarEventRow
{
    public string Time { get; init; } = "";
    public string Title { get; init; } = "";
    public string? Duration { get; init; }
    public string? Source { get; init; }
    public bool IsPast { get; init; }
}

public sealed class DashboardData
{
    public string Author { get; init; } = "";
    public string Initials { get; init; } = "?";
    public string FirstName { get; init; } = "there";
    public int Hour { get; init; }
    public string SyncedAt { get; init; } = "";
    public List<string> Dates { get; init; } = new();
    public string SelectedDate { get; init; } = "";
    public string SelectedDateLabel { get; init; } = "";
    public DashboardStats Stats { get; init; } = new();
    public List<TicketDayRow> Tickets { get; init; } = new();
    public List<OpenTicketRow> NeedsLogging { get; init; } = new();
    public List<TicketAction> ActivityFeed { get; init; } = new();
    public List<(string Ticket, TicketAction Action)> Activity { get; init; } = new();
    public List<DonutSlice> Donut { get; init; } = new();
    public string? CalendarSummary { get; init; }
    public List<CalendarEventRow> CalendarEvents { get; init; } = new();
    public string? JiraBaseUrl { get; init; }
}

public sealed class JiraIssueStatus
{
    public string? Status { get; init; }
    public string? StatusCategory { get; init; }
    public string? AssigneeEmail { get; init; }
}

public sealed class JiraTestResult
{
    public bool Ok { get; init; }
    public string? Error { get; init; }
    public string? DisplayName { get; init; }
}

public sealed class JiraUserSearchResult
{
    public string AccountId { get; init; } = "";
    public string DisplayName { get; init; } = "";
    public string Email { get; init; } = "";
    public string? AvatarUrl { get; init; }
}

public sealed class TeamMemberTicket
{
    public string Key { get; init; } = "";
    public string Summary { get; init; } = "";
    public string? Status { get; init; }
    public string? StatusCategory { get; init; }
    public int Minutes { get; init; }
    public string? JiraUrl { get; init; }
}

public sealed class TeamMemberWorklog
{
    public string AccountId { get; init; } = "";
    public string DisplayName { get; init; } = "";
    public string Email { get; init; } = "";
    public List<TeamMemberTicket> Tickets { get; init; } = new();
    public int TotalMinutes { get; init; }
}

public sealed class TeamWorklogResult
{
    public string Date { get; init; } = "";
    public string DateLabel { get; init; } = "";
    public List<TeamMemberWorklog> Members { get; init; } = new();
    public int DailyTargetMinutes { get; init; }
}

public sealed class BreakStatus
{
    public double ContinuousMinutes { get; init; }
    public int ReminderMinutes { get; init; }
}

public sealed class CalendarFetchResult
{
    public int Count { get; init; }
    public List<CalendarEventRaw> Events { get; init; } = new();
}

public sealed class CalendarEventRaw
{
    public string Title { get; init; } = "";
    public DateTimeOffset Start { get; init; }
    public DateTimeOffset? End { get; init; }
    public string? Source { get; init; }
}
