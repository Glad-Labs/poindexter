$models = (ollama list | Select-Object -Skip 1 | ForEach-Object {
    ($_ -split "\s+")[0]
})

$results = @()

foreach ($m in $models) {
    Write-Host "=== Benchmarking $m ==="

    $output = ollama run $m --verbose "benchmark test"

    $load = ($output | Select-String "load duration").ToString().Split(":")[1].Trim()
    $prompt = ($output | Select-String "prompt eval rate").ToString().Split(":")[1].Trim()
    $eval = ($output | Select-String "eval rate").ToString().Split(":")[1].Trim()
    $total = ($output | Select-String "total duration").ToString().Split(":")[1].Trim()

    $results += [PSCustomObject]@{
        Model = $m
        LoadDuration = $load
        PromptEvalRate = $prompt
        EvalRate = $eval
        TotalDuration = $total
    }
}

$results | Export-Csv -Path "ollama_benchmarks.csv" -NoTypeInformation
Write-Host "Benchmark complete. Results saved to ollama_benchmarks.csv"