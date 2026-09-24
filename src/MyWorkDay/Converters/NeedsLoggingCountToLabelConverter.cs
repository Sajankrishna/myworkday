using System.Globalization;
using System.Windows.Data;

namespace MyWorkDay.Converters;

/// <summary>Same pluralization the Python build's needsLoggingPill used:
/// "N ticket(s) need(s) time logged".</summary>
public sealed class NeedsLoggingCountToLabelConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
    {
        var n = value is int i ? i : 0;
        var ticketWord = n == 1 ? "ticket" : "tickets";
        var needWord = n == 1 ? "needs" : "need";
        return $"⚠ {n} {ticketWord} {needWord} time logged";
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotSupportedException();
}
