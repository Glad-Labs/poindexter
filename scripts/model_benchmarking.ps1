$OLLAMA_URL    = "http://localhost:11434"
$ITERATIONS    = 3      # Averaged runs per model (excludes warm-up)
$BENCHMARK_PROMPT = "Explain the difference between supervised and unsupervised machine learning in detail."
# Force all layers to GPU — with 32 GB VRAM most models fit entirely on the RTX 5090
$NUM_GPU_LAYERS = 99
$NUM_CTX        = 4096  # Context window; raise to 8192+ for large-context benchmarks

$script:ModelMetadata = @{}

function Convert-BytesToGiB {
    param([double]$Bytes)

    if ($Bytes -le 0) { return 0 }
    return [math]::Round($Bytes / 1GB, 2)
}

function Get-ModelMetadata {
    if ($script:ModelMetadata.Count -eq 0) {
        try {
            $tags = Invoke-RestMethod -Uri "$OLLAMA_URL/api/tags" -Method Get -TimeoutSec 30
            foreach ($model in $tags.models) {
                $script:ModelMetadata[$model.name] = $model
            }
        }
        catch {
            Write-Warning "Unable to retrieve model metadata from Ollama tags API: $_"
        }
    }

    return $script:ModelMetadata
}

$models = (ollama list | Select-Object -Skip 1 | ForEach-Object {
    ($_ -split "\s+")[0]
}) | Where-Object { $_ -ne "" }

function Invoke-OllamaBenchmark {
    param([string]$Model, [bool]$Warmup = $false)

    $body = @{
        model  = $Model
        prompt = $BENCHMARK_PROMPT
        stream = $false
        options = @{
            num_gpu = $NUM_GPU_LAYERS
            num_ctx = $NUM_CTX
        }
    } | ConvertTo-Json -Depth 3

    $response = Invoke-RestMethod `
        -Uri "$OLLAMA_URL/api/generate" `
        -Method Post `
        -ContentType "application/json" `
        -Body $body `
        -TimeoutSec 300

    if ($Warmup) { return }

    # Ollama returns durations in nanoseconds
    $ns = 1e9
    return [PSCustomObject]@{
        LoadSec        = [math]::Round($response.load_duration   / $ns, 3)
        TotalSec       = [math]::Round($response.total_duration  / $ns, 3)
        PromptTokensPS = if ($response.prompt_eval_duration -gt 0) {
                             [math]::Round($response.prompt_eval_count / ($response.prompt_eval_duration / $ns), 1)
                         } else { 0 }
        EvalTokensPS   = if ($response.eval_duration -gt 0) {
                             [math]::Round($response.eval_count / ($response.eval_duration / $ns), 1)
                         } else { 0 }
        EvalTokens     = $response.eval_count
    }
}

function New-ResultRow {
    param(
        [string]$Model,
        $Metadata,
        [int]$Iterations,
        [object]$Averages,
        [string]$Status,
        [string]$FailureReason = ""
    )

    $details = if ($Metadata -and $Metadata.details) { $Metadata.details } else { $null }
    $family = if ($details -and $details.family) { $details.family } elseif ($details -and $details.families) { ($details.families -join ",") } else { "Unknown" }
    $params = if ($details -and $details.parameter_size) { $details.parameter_size } else { "Unknown" }
    $quant = if ($details -and $details.quantization_level) { $details.quantization_level } else { "Unknown" }
    $sizeGiB = if ($Metadata -and $null -ne $Metadata.size) { Convert-BytesToGiB -Bytes ([double]$Metadata.size) } else { "Unknown" }

    return [PSCustomObject]@{
        Model               = $Model
        Family              = $family
        Parameters          = $params
        Quantization        = $quant
        "ModelSize(GiB)"    = $sizeGiB
        Status              = $Status
        "LoadDuration(s)"   = if ($Averages) { $Averages.LoadSec } else { "ERROR" }
        "TotalDuration(s)"  = if ($Averages) { $Averages.TotalSec } else { "ERROR" }
        "PromptEval(tok/s)" = if ($Averages) { $Averages.PromptTokensPS } else { "ERROR" }
        "EvalRate(tok/s)"   = if ($Averages) { $Averages.EvalTokensPS } else { "ERROR" }
        "EvalTokens(avg)"   = if ($Averages) { $Averages.EvalTokens } else { "ERROR" }
        "TokensPerGiB"      = if ($Averages -and $sizeGiB -is [double] -and $sizeGiB -gt 0) {
                                  [math]::Round($Averages.EvalTokensPS / $sizeGiB, 1)
                              } else { "N/A" }
        Iterations          = $Iterations
        FailureReason       = $FailureReason
    }
}

$results = @()
$metadataByModel = Get-ModelMetadata

foreach ($m in $models) {
    Write-Host "`n=== Benchmarking $m ===" -ForegroundColor Cyan
    $metadata = $metadataByModel[$m]

    try {
        # Warm-up: loads model into VRAM; not counted in averages
        Write-Host "  Warming up..." -NoNewline
        Invoke-OllamaBenchmark -Model $m -Warmup $true
        Write-Host " done"

        $runs = @()
        for ($i = 1; $i -le $ITERATIONS; $i++) {
            Write-Host "  Run $i/$ITERATIONS..." -NoNewline
            $run = Invoke-OllamaBenchmark -Model $m
            $runs += $run
            Write-Host " $($run.EvalTokensPS) tok/s"
        }

        $avgLoad    = [math]::Round(($runs | Measure-Object LoadSec    -Average).Average, 3)
        $avgTotal   = [math]::Round(($runs | Measure-Object TotalSec   -Average).Average, 3)
        $avgPrompt  = [math]::Round(($runs | Measure-Object PromptTokensPS -Average).Average, 1)
        $avgEval    = [math]::Round(($runs | Measure-Object EvalTokensPS   -Average).Average, 1)

        Write-Host "  Avg eval rate: $avgEval tok/s | Total: ${avgTotal}s" -ForegroundColor Green

        $averages = [PSCustomObject]@{
            LoadSec        = $avgLoad
            TotalSec       = $avgTotal
            PromptTokensPS = $avgPrompt
            EvalTokensPS   = $avgEval
            EvalTokens     = [math]::Round(($runs | Measure-Object EvalTokens -Average).Average, 0)
        }

        $results += New-ResultRow -Model $m -Metadata $metadata -Iterations $ITERATIONS -Averages $averages -Status "OK"
    }
    catch {
        $failureReason = $_.Exception.Message
        Write-Warning "  Failed to benchmark ${m}: $failureReason"
        $results += New-ResultRow -Model $m -Metadata $metadata -Iterations 0 -Status "ERROR" -FailureReason $failureReason
    }
}

$repoRoot = Split-Path $PSScriptRoot -Parent
$tmpDir = Join-Path $repoRoot ".tmp"
if (-not (Test-Path $tmpDir)) {
    New-Item -Path $tmpDir -ItemType Directory | Out-Null
}

$outPath = Join-Path $PSScriptRoot "ollama_benchmarks.csv"
$tmpCsvPath = Join-Path $tmpDir "ollama_benchmarks.csv"
$tmpSummaryPath = Join-Path $tmpDir "ollama_benchmark_summary.txt"

$results | Export-Csv -Path $outPath -NoTypeInformation
$results | Export-Csv -Path $tmpCsvPath -NoTypeInformation

$successfulResults = $results | Where-Object { $_.Status -eq "OK" }
$topThroughput = $successfulResults | Sort-Object "EvalRate(tok/s)" -Descending | Select-Object -First 10
$largestModels = $successfulResults | Where-Object { $_."ModelSize(GiB)" -is [double] } | Sort-Object "ModelSize(GiB)" -Descending | Select-Object -First 10

$summaryLines = @(
    "Ollama Benchmark Summary"
    "Generated: $(Get-Date -Format s)"
    "Prompt: $BENCHMARK_PROMPT"
    "Iterations per model: $ITERATIONS"
    "num_gpu: $NUM_GPU_LAYERS"
    "num_ctx: $NUM_CTX"
    "",
    "Top Throughput Models"
)

foreach ($item in $topThroughput) {
    $summaryLines += ("- {0} | {1} tok/s | {2} GiB | {3}" -f $item.Model, $item."EvalRate(tok/s)", $item."ModelSize(GiB)", $item.Quantization)
}

$summaryLines += ""
$summaryLines += "Largest Successfully Benchmarked Models"
foreach ($item in $largestModels) {
    $summaryLines += ("- {0} | {1} GiB | {2} tok/s | {3}" -f $item.Model, $item."ModelSize(GiB)", $item."EvalRate(tok/s)", $item.Parameters)
}

$failedResults = $results | Where-Object { $_.Status -eq "ERROR" }
if ($failedResults) {
    $summaryLines += ""
    $summaryLines += "Failures"
    foreach ($item in $failedResults) {
        $summaryLines += ("- {0} | {1}" -f $item.Model, $item.FailureReason)
    }
}

$summaryLines | Set-Content -Path $tmpSummaryPath

Write-Host "`n=== Results ===" -ForegroundColor Cyan
$results | Format-Table -AutoSize
Write-Host "`n=== Top Throughput ===" -ForegroundColor Cyan
$topThroughput | Format-Table Model, "ModelSize(GiB)", "EvalRate(tok/s)", Quantization -AutoSize
Write-Host "Benchmark complete. Results saved to $outPath"
Write-Host "CSV copy written to $tmpCsvPath"
Write-Host "Summary written to $tmpSummaryPath"