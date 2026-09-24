namespace MyWorkDay.Core;

/// <summary>Builds one day's DashboardData purely from Jira (worklogs you logged + comments you
/// wrote, both filtered to your own account and bucketed by the same Eastern/10pm-cutoff
/// work-day rule everywhere else) - the Jira-only equivalent of the Python build's
/// Api.refresh()/get_data(), with git scanning removed entirely.</summary>
public sealed class DashboardService
{
    public const int DailyTargetMinutes = 8 * 60;
    private const int CommentLookbackDays = 7;

    private readonly JiraService _jira;
    private readonly ConfigService _config;
    private readonly ErrorLogService _errorLog;

    // Cached across calls in one session so repeated date-jumps don't refetch account id.
    private string? _accountId;
    private string _displayName = "there";

    public DashboardService(JiraService jira, ConfigService config, ErrorLogService errorLog)
    {
        _jira = jira;
        _config = config;
        _errorLog = errorLog;
    }

    /// <summary>Full refresh: re-fetches everything for the given date (defaults to today).
    /// Call this for a manual "Rescan" or on the periodic auto-refresh timer.</summary>
    public async Task<DashboardData> RefreshAsync(string? dateKey = null)
    {
        var cfg = _config.Load();
        if (cfg.JiraConnected)
        {
            _accountId ??= await _jira.GetMyAccountIdAsync();
            _displayName = await _jira.GetDisplayNameAsync() ?? _displayName;
        }
        return await BuildAsync(dateKey, cfg);
    }

    /// <summary>Cheap re-render for a date-picker change - no network calls beyond what that
    /// date actually needs (worklogs/comments for the newly selected day).</summary>
    public Task<DashboardData> GetDataAsync(string? dateKey) => BuildAsync(dateKey, _config.Load());

    private async Task<DashboardData> BuildAsync(string? dateKey, AppConfig cfg)
    {
        var today = WorkDate.Today();
        dateKey = string.IsNullOrWhiteSpace(dateKey) ? today : dateKey;
        if (!DateTime.TryParseExact(dateKey, "yyyy-MM-dd", null, System.Globalization.DateTimeStyles.None, out _))
            dateKey = today;

        var rows = new Dictionary<string, TicketDayRow>();
        var openAssigned = new List<OpenTicketRow>();
        int weekLoggedMinutes = 0;
        int weekTickets = 0;

        if (cfg.JiraConnected)
        {
            try
            {
                var worklogRows = await _jira.GetMyWorklogRowsAsync(dateKey);
                foreach (var kv in worklogRows) rows[kv.Key] = kv.Value;

                if (_accountId != null)
                {
                    var commentedRows = await _jira.GetCommentedRowsAsync(_accountId, CommentLookbackDays, cfg.WatchedTickets);
                    foreach (var (key, commentRow) in commentedRows)
                    {
                        // Only merge in this ticket's comments that actually fall on the
                        // selected day - GetCommentedRowsAsync returns the whole lookback
                        // window's comments so a single fetch can serve every date jump.
                        var todaysComments = commentRow.Actions.Where(a => WorkDate.KeyFor(a.Timestamp) == dateKey).ToList();
                        if (todaysComments.Count == 0) continue;
                        if (rows.TryGetValue(key, out var existing))
                        {
                            var merged = existing.Actions.Concat(todaysComments).OrderBy(a => a.Timestamp).ToList();
                            rows[key] = new TicketDayRow
                            {
                                Key = existing.Key,
                                Summary = existing.Summary,
                                Status = existing.Status,
                                StatusCategory = existing.StatusCategory,
                                LoggedMinutes = existing.LoggedMinutes,
                                ExpectedMinutes = existing.ExpectedMinutes ?? commentRow.ExpectedMinutes,
                                JiraUrl = existing.JiraUrl,
                                Actions = merged,
                            };
                        }
                        else
                        {
                            rows[key] = new TicketDayRow
                            {
                                Key = commentRow.Key,
                                Summary = commentRow.Summary,
                                Status = commentRow.Status,
                                StatusCategory = commentRow.StatusCategory,
                                LoggedMinutes = 0,
                                ExpectedMinutes = commentRow.ExpectedMinutes,
                                JiraUrl = commentRow.JiraUrl,
                                Actions = todaysComments,
                            };
                        }
                    }
                }

                // Fetched only for the "open tickets assigned to you" stat and (indirectly)
                // for the needs-logging list's real status/summary data - NOT merged into "My
                // Work" itself. My Work is real day-scoped activity only (a worklog entry or a
                // comment on the selected day), never an assignment with nothing behind it.
                openAssigned = await _jira.GetMyOpenAssignedAsync();

                // Week total: sum this same day-bucket rule across the last 7 calendar days,
                // one worklog fetch per day - small, bounded fan-out.
                var weekDates = Enumerable.Range(0, 7).Select(i => WorkDate.KeyFor(DateTimeOffset.UtcNow.AddDays(-i))).Distinct();
                var weekTasks = weekDates.Select(d => _jira.GetMyWorklogRowsAsync(d));
                foreach (var dayRows in await Task.WhenAll(weekTasks))
                {
                    weekLoggedMinutes += dayRows.Values.Sum(r => r.LoggedMinutes);
                    weekTickets += dayRows.Count;
                }
            }
            catch (Exception ex)
            {
                _errorLog.Log("DashboardService.BuildAsync failed", ex);
            }
        }

        var ticketRows = rows.Values.OrderByDescending(r => r.LastActivity).ToList();
        var loggedMinutes = ticketRows.Sum(r => r.LoggedMinutes);
        var commentsMade = ticketRows.Sum(r => r.Actions.Count(a => a.Kind == "comment"));

        // Tickets that need attention today: everything real in "My Work" (comment or worklog
        // activity) with no worklog time on it yet - in practice, tickets you commented on but
        // haven't logged time for.
        var needsLogging = dateKey == today
            ? ticketRows.Where(r => r.LoggedMinutes == 0)
                .Select(r => new OpenTicketRow { Key = r.Key, Summary = r.Summary, Status = r.Status, StatusCategory = r.StatusCategory, JiraUrl = r.JiraUrl, ExpectedMinutes = r.ExpectedMinutes })
                .ToList()
            : new List<OpenTicketRow>();

        var activity = ticketRows
            .SelectMany(r => r.Actions.Select(a => (Ticket: r.Key, Action: a)))
            .OrderByDescending(x => x.Action.Timestamp)
            .Take(20)
            .ToList();

        var catMins = new Dictionary<string, int>();
        foreach (var r in ticketRows)
        {
            if (r.LoggedMinutes <= 0) continue;
            var label = r.Status ?? "No status";
            catMins[label] = catMins.GetValueOrDefault(label) + r.LoggedMinutes;
        }
        var donutTotal = catMins.Values.Sum();
        var donut = catMins.Select(kv => new DonutSlice
            {
                Label = kv.Key,
                Minutes = kv.Value,
                Pct = donutTotal > 0 ? (int)Math.Round(100.0 * kv.Value / donutTotal) : 0,
            })
            .OrderByDescending(d => d.Minutes).Take(8).ToList();

        var dates = new List<string> { today };
        if (!dates.Contains(dateKey)) dates.Add(dateKey);

        var initials = _displayName != "there" && !string.IsNullOrWhiteSpace(_displayName)
            ? string.Concat(_displayName.Split(' ', StringSplitOptions.RemoveEmptyEntries).Take(2).Select(w => char.ToUpper(w[0])))
            : "?";

        return new DashboardData
        {
            Author = _displayName,
            Initials = initials,
            FirstName = _displayName.Split(' ', StringSplitOptions.RemoveEmptyEntries).FirstOrDefault() ?? "there",
            Hour = DateTime.Now.Hour,
            SyncedAt = DateTime.Now.ToString("h:mm tt").TrimStart('0'),
            Dates = dates.OrderDescending().ToList(),
            SelectedDate = dateKey,
            SelectedDateLabel = WorkDate.Parse(dateKey).ToString("ddd, MMM d, yyyy"),
            Stats = new DashboardStats
            {
                LoggedMinutes = loggedMinutes,
                DailyTargetMinutes = DailyTargetMinutes,
                TargetPct = DailyTargetMinutes > 0 ? Math.Min(100, (int)Math.Round(100.0 * loggedMinutes / DailyTargetMinutes)) : 0,
                TicketsTouched = ticketRows.Count,
                CommentsMade = commentsMade,
                OpenAssigned = openAssigned.Count,
                WeekLoggedMinutes = weekLoggedMinutes,
                WeekTickets = weekTickets,
            },
            Tickets = ticketRows,
            NeedsLogging = needsLogging,
            Activity = activity,
            Donut = donut,
            JiraBaseUrl = cfg.JiraBaseUrl,
        };
    }
}
