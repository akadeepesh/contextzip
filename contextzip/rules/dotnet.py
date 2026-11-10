"""
C# / F# / .NET (MSBuild, NuGet) exclusion rules.
"""

PATTERNS = [
    # Build output
    "bin/",
    "obj/",
    "artifacts/",
    "publish/",
    "TestResults/",
    "BenchmarkDotNet.Artifacts/",
    # Compiled and packaged
    "*.pdb",
    "*.nupkg",
    "*.snupkg",
    "*.cache",
    # Generated code
    "*.g.cs",
    "*.g.i.cs",
    "*.generated.cs",
    "Migrations/*.Designer.cs",
    # NuGet
    ".nuget/",
    "packages.lock.json",
    "project.assets.json",
    # Visual Studio / Rider
    ".vs/",
    "*.user",
    "*.suo",
    "*.DotSettings.user",
    "_ReSharper*/",
    # Client libraries restored by LibMan
    "wwwroot/lib/",
]
