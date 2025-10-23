# Runs montag in Docker for every file in _explicit_files and writes results to _cleaned_files
# Then deletes the processed files from _explicit_files.
param(
    [string]$ExplicitDir = (Join-Path (Split-Path $PSScriptRoot -Parent) "_explicit_files"),
    [string]$CleanDir    = (Join-Path (Split-Path $PSScriptRoot -Parent) "_cleaned_files"),
    [string]$Image       = "oci.guero.org/montag:latest",
    [string]$Engine      = "docker",
    [string]$Encoding    = "utf-8",
    [string]$SwearsFile  = ""
)

# Allow env vars to override defaults
if ($env:MONTAG_IMAGE) { $Image = $env:MONTAG_IMAGE }
if ($env:CONTAINER_ENGINE) { $Engine = $env:CONTAINER_ENGINE }

Write-Host "Explicit dir: $ExplicitDir"
Write-Host "Clean dir:    $CleanDir"
Write-Host "Image:        $Image"
Write-Host "Engine:       $Engine"
Write-Host "Encoding:     $Encoding"
if ($SwearsFile) { Write-Host "Swears file:  $SwearsFile" }

if (-not (Test-Path -LiteralPath $ExplicitDir)) {
    throw "Explicit directory not found: $ExplicitDir"
}

if (-not (Test-Path -LiteralPath $CleanDir)) {
    Write-Host "Creating clean directory: $CleanDir"
    New-Item -ItemType Directory -Path $CleanDir | Out-Null
}

$files = Get-ChildItem -LiteralPath $ExplicitDir -File -ErrorAction Stop
if (-not $files) {
    Write-Host "No files found in '$ExplicitDir'. Nothing to do."
    exit 0
}

function New-TempDir {
    $guid = [System.Guid]::NewGuid().ToString('N')
    $path = Join-Path ([System.IO.Path]::GetTempPath()) ("montag_" + $guid)
    New-Item -ItemType Directory -Path $path -Force | Out-Null
    return $path
}

$overallFailures = 0

foreach ($file in $files) {
    Write-Host "Processing: $($file.Name)" -ForegroundColor Cyan
    $tempDir = $null
    try {
        $tempDir = New-TempDir
        $inBase  = $file.Name
        $outBase = $file.Name  # keep same name; montag will adjust extension if needed

        Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $tempDir $inBase) -Force

        $swearsArg = @()
        if ($SwearsFile -and (Test-Path -LiteralPath $SwearsFile)) {
            Copy-Item -LiteralPath $SwearsFile -Destination (Join-Path $tempDir "swears.txt") -Force
            # Pass explicit word list path inside container
            $swearsArg = @('-w', '/data/swears.txt')
        }

        $dockerArgs = @(
            'run','--rm','-t',
            '-v', ("$($tempDir):/data:rw"),
            $Image,
            '-i', ("/data/$inBase"),
            '-o', ("/data/$outBase"),
            '-e', $Encoding
        ) + $swearsArg

        # Invoke container
        $p = Start-Process -FilePath $Engine -ArgumentList $dockerArgs -NoNewWindow -PassThru -Wait
        if ($p.ExitCode -ne 0) {
            throw "Container exited with code $($p.ExitCode)"
        }

        $outPathTemp = Join-Path $tempDir $outBase
        if (-not (Test-Path -LiteralPath $outPathTemp)) {
            # If montag adjusted the extension, try to find a file with same stem
            $stem = [System.IO.Path]::GetFileNameWithoutExtension($outBase)
            $found = Get-ChildItem -LiteralPath $tempDir -File | Where-Object { $_.BaseName -eq $stem } | Select-Object -First 1
            if ($found) { $outPathTemp = $found.FullName }
        }

        if (-not (Test-Path -LiteralPath $outPathTemp)) {
            throw "Output file not produced in temp dir: $tempDir"
        }

        $destPath = Join-Path $CleanDir ([System.IO.Path]::GetFileName($outPathTemp))

        # If destination exists with same name, append " (scrubbed)"
        if (Test-Path -LiteralPath $destPath) {
            $stem = [System.IO.Path]::GetFileNameWithoutExtension($destPath)
            $ext  = [System.IO.Path]::GetExtension($destPath)
            $destPath = Join-Path $CleanDir ("$stem (scrubbed)$ext")
        }

        Copy-Item -LiteralPath $outPathTemp -Destination $destPath -Force
        Write-Host "Saved: $destPath" -ForegroundColor Green

        # Delete original from explicit folder
        Remove-Item -LiteralPath $file.FullName -Force
        Write-Host "Deleted original: $($file.FullName)" -ForegroundColor DarkYellow
    }
    catch {
        $overallFailures++
        Write-Warning "Failed to process '$($file.Name)': $($_.Exception.Message)"
    }
    finally {
        if ($tempDir -and (Test-Path -LiteralPath $tempDir)) {
            Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

if ($overallFailures -gt 0) {
    Write-Host "Completed with $overallFailures failures." -ForegroundColor Red
    exit 1
}
else {
    Write-Host "All files processed successfully." -ForegroundColor Green
}
