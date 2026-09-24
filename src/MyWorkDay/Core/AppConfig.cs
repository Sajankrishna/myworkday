using System.Text.Json.Serialization;

namespace MyWorkDay.Core;

/// <summary>
/// Persisted settings, mirrored 1:1 against the original Python app's config.json schema
/// (%LOCALAPPDATA%\MyWorkDay\config.json) so an existing install's saved Jira/Google/team
/// settings keep working after switching to this WPF build - same keys, same file, same
/// plaintext-on-disk tradeoff (see ConfigService for the "why plaintext" reasoning).
/// </summary>
public sealed class AppConfig
{
    [JsonPropertyName("JIRA_BASE_URL")]
    public string? JiraBaseUrl { get; set; }

    [JsonPropertyName("JIRA_EMAIL")]
    public string? JiraEmail { get; set; }

    [JsonPropertyName("JIRA_API_TOKEN")]
    public string? JiraApiToken { get; set; }

    [JsonPropertyName("GOOGLE_CLIENT_ID")]
    public string? GoogleClientId { get; set; }

    [JsonPropertyName("GOOGLE_CLIENT_SECRET")]
    public string? GoogleClientSecret { get; set; }

    [JsonPropertyName("GOOGLE_REFRESH_TOKEN")]
    public string? GoogleRefreshToken { get; set; }

    [JsonPropertyName("BREAK_REMINDER_MINS")]
    public int? BreakReminderMins { get; set; }

    [JsonPropertyName("TEAM_MEMBERS")]
    public List<TeamMemberConfig> TeamMembers { get; set; } = new();

    // Extra ticket keys to check for your own comments even when you're neither assignee nor
    // reporter (e.g. commenting on a teammate's ticket) - GetCommentedRowsAsync's own JQL
    // candidate search is scoped to assignee/reporter = you for a real reason (this Jira
    // instance updates 100+ tickets a day; scanning every recently-updated ticket for your
    // comments isn't practical), so this is the deliberate, bounded escape hatch: tickets you
    // explicitly say to also watch.
    [JsonPropertyName("WATCHED_TICKETS")]
    public List<string> WatchedTickets { get; set; } = new();

    [JsonIgnore]
    public bool JiraConnected => !string.IsNullOrWhiteSpace(JiraBaseUrl)
        && !string.IsNullOrWhiteSpace(JiraEmail)
        && !string.IsNullOrWhiteSpace(JiraApiToken);

    [JsonIgnore]
    public bool CalendarConnected => !string.IsNullOrWhiteSpace(GoogleRefreshToken);
}

public sealed class TeamMemberConfig
{
    [JsonPropertyName("account_id")]
    public string AccountId { get; set; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; set; } = "";

    [JsonPropertyName("email")]
    public string Email { get; set; } = "";
}
