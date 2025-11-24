using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;

if (args.Length < 4)
{
    Console.WriteLine("Usage:");
    Console.WriteLine("  dotnet run <rootDir> <baseUrl> <deviceType> <firmwareVersion>");
    Console.WriteLine();
    Console.WriteLine("Example:");
    Console.WriteLine("  dotnet run C:\\Daniel\\Projects\\pengy\\OTA\\v1 http://server/pengy/ota/v1 pengy-wifi 2025-11-21_01");
    return;
}

string rootDir = Path.GetFullPath(args[0]);
string baseUrl = args[1].TrimEnd('/');
string deviceType = args[2];
string version = args[3];

Console.WriteLine($"Root: {rootDir}");
Console.WriteLine($"BaseUrl: {baseUrl}");
Console.WriteLine($"Device: {deviceType}");
Console.WriteLine($"Version: {version}");

if (!Directory.Exists(rootDir))
{
    Console.WriteLine("ERROR: Directory does not exist.");
    return;
}

var otaFiles = Directory.GetFiles(rootDir, "*.ota", SearchOption.AllDirectories);
var files = new List<ManifestFile>();
long totalSize = 0;

foreach (var fullPath in otaFiles)
{
    var relPath = Path.GetRelativePath(rootDir, fullPath)
                      .Replace("\\", "/");

    var devicePath = relPath.EndsWith(".ota", StringComparison.OrdinalIgnoreCase)
        ? relPath[..^4]
        : relPath;

    string url;
    if (!string.IsNullOrEmpty(baseUrl))
    {
        // relativni URL (samo ime .ota fajla)
        url = relPath;
    }
    else
    {
        // baseUrl prazan → url je apsolutan (kao dosad)
        url = relPath.StartsWith("http") ? relPath : $"{relPath}";
    }

    var fi = new FileInfo(fullPath);
    long size = fi.Length;
    totalSize += size;

    var sha = ComputeSha256Hex(fullPath);

    files.Add(new ManifestFile
    {
        Path = devicePath,
        Url = url,
        Size = size,
        Sha256 = sha
    });

    Console.WriteLine($" + {devicePath}  ({size} bytes)  sha256={sha}");
}

var manifest = new ManifestRoot
{
    DeviceType = deviceType,
    FirmwareVersion = version,
    TotalSize = totalSize,
    BaseUrl = baseUrl,
    Files = files
};

var json = JsonSerializer.Serialize(manifest, new JsonSerializerOptions { WriteIndented = true });

File.WriteAllText(Path.Combine(rootDir, "manifest.json"), json);

Console.WriteLine();
Console.WriteLine("Manifest saved!");


// ==== helper funkcije ====

static string ComputeSha256Hex(string path)
{
    using var sha = SHA256.Create();
    using var stream = File.OpenRead(path);
    var hash = sha.ComputeHash(stream);
    return BitConverter.ToString(hash).Replace("-", "").ToLowerInvariant();
}

class ManifestRoot
{
    [JsonPropertyName("device_type")] public string DeviceType { get; set; }
    [JsonPropertyName("firmware_version")] public string FirmwareVersion { get; set; }
    [JsonPropertyName("total_size")] public long TotalSize { get; set; }

    [JsonPropertyName("base_url")] public string BaseUrl { get; set; } // NEW

    [JsonPropertyName("files")] public List<ManifestFile> Files { get; set; }
}

class ManifestFile
{
    [JsonPropertyName("path")] public string Path { get; set; }
    [JsonPropertyName("url")] public string Url { get; set; }
    [JsonPropertyName("size")] public long Size { get; set; }
    [JsonPropertyName("sha256")] public string Sha256 { get; set; }
}
