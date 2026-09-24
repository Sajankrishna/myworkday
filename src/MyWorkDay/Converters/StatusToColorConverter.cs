using System.Globalization;
using System.Windows.Data;
using System.Windows.Media;
using MyWorkDay.Controls;

namespace MyWorkDay.Converters;

/// <summary>Gives every distinct Jira status name its own color (deterministic hash into the
/// same palette the donut chart uses), instead of only the 3-way new/indeterminate/done
/// grouping - "In Dev", "In Review", "Ready for QA", "Dev Blocked" etc. are all visually
/// distinct at a glance, not just "still in progress" vs "done".</summary>
public sealed class StatusToColorConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
    {
        var status = value as string;
        if (string.IsNullOrWhiteSpace(status)) return Brushes.Gray;
        uint h = 2166136261;
        foreach (var ch in status) h = (h ^ ch) * 16777619; // FNV-1a - stable across runs
        var color = DonutChart.Palette[(int)(h % (uint)DonutChart.Palette.Length)];
        return new SolidColorBrush(color);
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotSupportedException();
}
