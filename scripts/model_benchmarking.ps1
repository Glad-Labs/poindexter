$OLLAMA_URL    = "http://localhost:11434"
$ITERATIONS    = 3      # Averaged runs per model (excludes warm-up)
$BENCHMARK_PROMPT = "Explain the difference between supervised and unsupervised machine learning in detail."
# Force all layers to GPU — with 32 GB VRAM most models fit entirely on the RTX 5090
$NUM_GPU_LAYERS = 99
$NUM_CTX        = 4096  # Context window; raise to 8192+ for large-context benchmarks

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

$results = @()

foreach ($m in $models) {
    Write-Host "`n=== Benchmarking $m ===" -ForegroundColor Cyan

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

        $results += [PSCustomObject]@{
            Model              = $m
            "LoadDuration(s)"  = $avgLoad
            "TotalDuration(s)" = $avgTotal
            "PromptEval(tok/s)"= $avgPrompt
            "EvalRate(tok/s)"  = $avgEval
            Iterations         = $ITERATIONS
        }
    }
    catch {
        Write-Warning "  Failed to benchmark ${m}: $_"
        $results += [PSCustomObject]@{
            Model               = $m
            "LoadDuration(s)"   = "ERROR"
            "TotalDuration(s)"  = "ERROR"
            "PromptEval(tok/s)" = "ERROR"
            "EvalRate(tok/s)"   = "ERROR"
            Iterations          = 0
        }
    }
}

$outPath = Join-Path $PSScriptRoot "ollama_benchmarks.csv"
$results | Export-Csv -Path $outPath -NoTypeInformation

Write-Host "`n=== Results ===" -ForegroundColor Cyan
$results | Format-Table -AutoSize
Write-Host "Benchmark complete. Results saved to $outPath"