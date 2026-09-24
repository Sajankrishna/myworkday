using System.Globalization;
using System.Windows.Data;
using MyWorkDay.Core;

namespace MyWorkDay.Converters;

/// <summary>Formats a nullable minute count ("Jira's Original Estimate", never invented) as
/// "Xh Ym" - null (unset in Jira) renders as empty text, paired with NullToVisibilityConverter
/// to hide the whole row when there's nothing real to show.</summary>
public sealed class MinutesLabelConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
        => value is int mins ? DashboardStats.FormatMinutes(mins) : "";

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotSupportedException();
}
