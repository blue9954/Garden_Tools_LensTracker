using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows.Forms;
using System.Reflection;

[assembly: AssemblyTitle("Garden_Tools_LensTracker")]
[assembly: AssemblyProduct("Garden_Tools_LensTracker")]
[assembly: AssemblyDescription("LensTracker desktop launcher")]

internal static class Launcher
{
    // Escape every argument according to Windows command-line parsing rules.
    private static string Quote(string argument)
    {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char character in argument)
        {
            if (character == '\\')
            {
                slashes++;
                continue;
            }
            result.Append('\\', character == '"' ? slashes * 2 + 1 : slashes);
            result.Append(character);
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        return result.Append('"').ToString();
    }

    [STAThread]
    private static int Main(string[] args)
    {
        string folder = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "dist", "LensTracker");
        string executable = Path.Combine(folder, "LensTracker.exe");
        try
        {
            if (!File.Exists(executable))
                throw new FileNotFoundException("앱 실행 파일을 찾을 수 없습니다. 프로젝트의 dist 폴더를 함께 유지해 주세요.", executable);
            var start = new ProcessStartInfo(executable, string.Join(" ", args.Select(Quote)))
            {
                WorkingDirectory = folder,
                UseShellExecute = false,
                CreateNoWindow = true
            };
            using (var process = Process.Start(start))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "Garden_Tools_LensTracker 실행 오류",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
