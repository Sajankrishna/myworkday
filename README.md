# MyWorkDay

A lightweight Windows desktop dashboard that mirrors your day's git activity against Jira, shows real logged time (not guesses), and gives you a quick "Today's Agenda" view from Google Calendar - all in one native window, no browser tab required.

## Features

- **My Work** - tickets touched today, pulled straight from your local git history and matched to Jira
- **Real logged time** - shows actual Jira worklog minutes per ticket, not an estimate
- **Time Breakdown** - a donut chart of logged time by real Jira status category
- **Needs Logging** - flags tickets you worked on/commented/reviewed that still have zero time logged
- **Today's Agenda** - your Google Calendar events for the day, with past ones grayed out
- **Break reminders** - a Windows toast if you've been continuously active (real mouse/keyboard input) for too long without a break
- **Team Worklogs** - search Jira users, add teammates, and see their logged time (with a per-day "how full is their day" progress bar) without leaving the app

## Download

Grab the latest installer from the [Releases](../../releases) page - `MyWorkDay-Setup-<version>.exe`. It's a normal per-user installer (no admin rights needed), with a Start Menu shortcut and a clean uninstaller.

## First-time setup

On first launch, open **Settings** to connect:
- **Jira** - your Jira Cloud base URL, email, and an [API token](https://id.atlassian.com/manage-profile/security/api-tokens)
- **Google Calendar** (optional) - an OAuth Client ID/Secret from the [Google Cloud Console](https://console.cloud.google.com/apis/credentials) (Desktop app type, Calendar API enabled)

Everything is stored locally in `%LOCALAPPDATA%\MyWorkDay\config.json` - nothing is sent anywhere except Jira/Google themselves.

## Updates

New versions are published as [GitHub Releases](../../releases). Download and run the latest installer over your existing install to update.
