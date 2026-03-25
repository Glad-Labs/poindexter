param(
    [string[]]$Models = @(),
    [int]$MaxModels = 0,
    [string]$JudgeModel = "",
    [switch]$DisableJudge,
    [switch]$SkipEmbeddings,
    [int]$MaxRetriesPerTask = 1,
    [switch]$RetryOnZeroScore,
    [int]$SnapshotRetention = 20,
    [int]$TimeoutSec = 240,
    [string]$OllamaUrl = "http://localhost:11434"
)

$ErrorActionPreference = "Stop"

if ($MaxRetriesPerTask -lt 0) {
    throw "MaxRetriesPerTask cannot be negative."
}

if ($SnapshotRetention -lt 1) {
    throw "SnapshotRetention must be at least 1."
}

# Default behavior: retry once when a task scores 0.
if (-not $PSBoundParameters.ContainsKey('RetryOnZeroScore')) {
    $RetryOnZeroScore = $true
}

function Invoke-OllamaGenerate {
    param(
        [string]$Model,
        [string]$Prompt,
        [double]$Temperature,
        [int]$NumCtx,
        [int]$NumGpu,
        [int]$Timeout
    )

    $body = @{
        model  = $Model
        prompt = $Prompt
        stream = $false
        options = @{
            temperature = $Temperature
            num_ctx     = $NumCtx
            num_gpu     = $NumGpu
        }
    } | ConvertTo-Json -Depth 4

    return Invoke-RestMethod `
        -Uri "$OllamaUrl/api/generate" `
        -Method Post `
        -ContentType "application/json" `
        -Body $body `
        -TimeoutSec $Timeout
}

function Get-RegisteredModels {
    $tags = Invoke-RestMethod -Uri "$OllamaUrl/api/tags" -Method Get -TimeoutSec 30
    if (-not $tags.models -or $tags.models.Count -eq 0) {
        throw "No models are currently registered in Ollama."
    }

    return @($tags.models | ForEach-Object { $_.name })
}

function Get-ModelMetadata {
    $tags = Invoke-RestMethod -Uri "$OllamaUrl/api/tags" -Method Get -TimeoutSec 30
    $map = @{}
    foreach ($m in $tags.models) {
        $sizeGiB = if ($null -ne $m.size) { [math]::Round(([double]$m.size / 1GB), 2) } else { $null }
        $family = if ($m.details -and $m.details.family) { $m.details.family } elseif ($m.details -and $m.details.families) { ($m.details.families -join ",") } else { "Unknown" }
        $params = if ($m.details -and $m.details.parameter_size) { $m.details.parameter_size } else { "Unknown" }
        $quant = if ($m.details -and $m.details.quantization_level) { $m.details.quantization_level } else { "Unknown" }

        $map[$m.name] = [PSCustomObject]@{
            SizeGiB      = $sizeGiB
            Family       = $family
            Parameter    = $params
            Quantization = $quant
        }
    }
    return $map
}

function Test-IsEmbeddingModel {
    param(
        [string]$ModelName,
        $Meta
    )

    if (-not $Meta) {
        # Conservative fallback for names when metadata is unavailable
        return ($ModelName -match '(?i)embed|embedding')
    }

    $family = if ($Meta.Family) { [string]$Meta.Family } else { "" }
    $parameter = if ($Meta.Parameter) { [string]$Meta.Parameter } else { "" }

    return (
        $ModelName -match '(?i)embed|embedding' -or
        $family -match '(?i)embed|embedding' -or
        $parameter -match '(?i)embed|embedding'
    )
}

function Score-Coding {
    param([string]$Text)

    $score = 0
    $checks = @{}
    $checks.HasFunction = $Text -match 'def\s+fibonacci\s*\('
    $checks.TypeHint = $Text -match '->\s*int'
    $checks.NegativeGuard = $Text -match 'n\s*<\s*0' -and $Text -match 'ValueError'
    $checks.NoRecursion = ([regex]::Matches($Text, 'fibonacci\s*\(').Count -le 1)
    $checks.ReturnPath = $Text -match '\breturn\b'

    if ($checks.HasFunction) { $score += 2 }
    if ($checks.TypeHint) { $score += 2 }
    if ($checks.NegativeGuard) { $score += 2 }
    if ($checks.NoRecursion) { $score += 2 }
    if ($checks.ReturnPath) { $score += 2 }

    return [PSCustomObject]@{ Score = $score; Notes = ($checks | ConvertTo-Json -Compress) }
}

function Score-Reasoning {
    param([string]$Text)

    # Puzzle answer is 42.
    $exactLine = $Text -match '(?im)^\s*ANSWER\s*=\s*42\s*$'
    $hasClear42 = $Text -match '(?i)(retained\s+signups?.{0,25}42|\b42\b)'
    if ($exactLine) {
        return [PSCustomObject]@{ Score = 10; Notes = "Exact format answer" }
    }
    if ($hasClear42) {
        return [PSCustomObject]@{ Score = 6; Notes = "Correct value but wrong output format" }
    }
    return [PSCustomObject]@{ Score = 0; Notes = "Incorrect value or missing 42" }
}

function Score-InstructionFollowing {
    param([string]$Text)

    $score = 0
    $notes = @()
    try {
        $jsonCandidate = $Text.Trim()
        if ($jsonCandidate -notmatch '^\s*\{') {
            $match = [regex]::Match($jsonCandidate, '\{[\s\S]*\}')
            if ($match.Success) {
                $jsonCandidate = $match.Value
            }
        }

        $obj = $jsonCandidate | ConvertFrom-Json

        $hasTitle = $obj.PSObject.Properties.Name -contains "title"
        $hasTags = $obj.PSObject.Properties.Name -contains "tags"
        $hasWordCount = $obj.PSObject.Properties.Name -contains "word_count"
        $hasExtra = ($obj.PSObject.Properties.Count -ne 3)

        if ($hasTitle) { $score += 3 } else { $notes += "Missing title" }
        if ($hasTags -and $obj.tags -is [System.Array] -and $obj.tags.Count -eq 3) { $score += 3 } else { $notes += "tags must be an array of exactly 3" }
        if ($hasWordCount -and $obj.word_count -is [int]) { $score += 3 } else { $notes += "word_count must be integer" }
        if (-not $hasExtra) { $score += 1 } else { $notes += "Extra keys found" }
    }
    catch {
        $notes += "Invalid JSON"
    }

    if ($notes.Count -eq 0) { $notes += "Valid strict JSON" }
    return [PSCustomObject]@{ Score = $score; Notes = ($notes -join "; ") }
}

function Score-CreativeWriting {
    param([string]$Text)

    $score = 0
    $notes = @()
    $wordCount = ([regex]::Matches($Text, '\b\w+\b')).Count
    $questionCount = ([regex]::Matches($Text, '\?')).Count
    $hasAICoFounder = $Text -match '(?i)AI co-founder'
    $hasCTA = $Text -match '(?m)^\s*Try\b'
    $hasMetaphor = $Text -match '(?i)like a|as if|compass|engine|blueprint|flywheel'
    $sentenceCount = [math]::Max(([regex]::Matches($Text, '[.!?]')).Count, 1)
    $avgSentenceLength = [math]::Round($wordCount / $sentenceCount, 1)

    if ($wordCount -ge 180 -and $wordCount -le 240) { $score += 3 } else { $notes += "Word count outside 180-240 ($wordCount)" }
    if ($questionCount -eq 1) { $score += 2 } else { $notes += "Need exactly one rhetorical question (found $questionCount)" }
    if ($hasAICoFounder) { $score += 2 } else { $notes += "Missing phrase: AI co-founder" }
    if ($hasCTA) { $score += 2 } else { $notes += "Missing CTA line starting with Try" }
    if ($hasMetaphor) { $score += 1 } else { $notes += "No vivid metaphor signal" }

    if ($notes.Count -eq 0) { $notes += "Creative constraints satisfied" }

    return [PSCustomObject]@{
        Score = $score
        Notes = ($notes -join "; ")
        WordCount = $wordCount
        AvgSentenceLength = $avgSentenceLength
    }
}

function Score-CreativeLongform {
    param([string]$Text)

    $score = 0
    $notes = @()

    $wordCount = ([regex]::Matches($Text, '\b\w+\b')).Count
    $h2Count = ([regex]::Matches($Text, '(?m)^##\s+')).Count
    $bulletCount = ([regex]::Matches($Text, '(?m)^-\s+')).Count
    $hasFounderStory = $Text -match '(?i)founder|when we shipped|we launched|first week|customer call'
    $hasConcreteExample = $Text -match '(?i)example|for instance|case study|experiment'
    $hasActionSteps = $Text -match '(?i)next step|checklist|playbook|how to'
    $hasNoHypeTone = -not ($Text -match '(?i)revolutionary|game-?changing|mind-?blowing')

    if ($wordCount -ge 380 -and $wordCount -le 560) { $score += 3 } else { $notes += "Word count outside 380-560 ($wordCount)" }
    if ($h2Count -ge 2) { $score += 2 } else { $notes += "Need at least 2 H2 headings" }
    if ($bulletCount -ge 3) { $score += 1 } else { $notes += "Need at least 3 bullet points" }
    if ($hasFounderStory) { $score += 2 } else { $notes += "Missing founder-grounded anecdote" }
    if ($hasConcreteExample) { $score += 1 } else { $notes += "Missing concrete example language" }
    if ($hasActionSteps) { $score += 1 } else { $notes += "Missing action-oriented guidance" }

    if (-not $hasNoHypeTone) {
        $score = [math]::Max($score - 1, 0)
        $notes += "Hypey phrasing detected"
    }

    if ($notes.Count -eq 0) { $notes += "Long-form creative constraints satisfied" }

    return [PSCustomObject]@{
        Score = $score
        Notes = ($notes -join "; ")
        WordCount = $wordCount
        H2Count = $h2Count
        BulletCount = $bulletCount
    }
}

function Get-JudgeScore {
    param(
        [string]$Judge,
        [string]$TargetModel,
        [string]$TaskName,
        [string]$Prompt,
        [string]$Response,
        [int]$Timeout
    )

    $judgePrompt = @"
You are an impartial evaluator. Score the model response from 1 to 10.

Task: $TaskName
Original Prompt:
$Prompt

Model Response:
$Response

Return strict JSON only with fields:
{"score": <1-10 integer>, "reason": "<one sentence>"}
"@

    try {
        $r = Invoke-OllamaGenerate -Model $Judge -Prompt $judgePrompt -Temperature 0.1 -NumCtx 4096 -NumGpu 99 -Timeout $Timeout
        $json = $r.response | ConvertFrom-Json
        $score = [math]::Min([math]::Max([int]$json.score, 1), 10)
        return [PSCustomObject]@{ Score = $score; Reason = $json.reason }
    }
    catch {
        return [PSCustomObject]@{ Score = $null; Reason = "Judge unavailable: $($_.Exception.Message)" }
    }
}

if (-not $Models -or $Models.Count -eq 0) {
    $Models = Get-RegisteredModels
}

$modelsUnique = @($Models | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique)
if ($MaxModels -gt 0) {
    $modelsToRun = @($modelsUnique | Select-Object -First $MaxModels)
}
else {
    $modelsToRun = $modelsUnique
}

if (-not $modelsToRun -or $modelsToRun.Count -eq 0) {
    throw "No models selected to run."
}
$metadataMap = Get-ModelMetadata

if ($SkipEmbeddings) {
    $beforeCount = $modelsToRun.Count
    $modelsToRun = @(
        $modelsToRun | Where-Object {
            $modelName = $_
            $meta = $metadataMap[$modelName]
            -not (Test-IsEmbeddingModel -ModelName $modelName -Meta $meta)
        }
    )

    $skippedCount = $beforeCount - $modelsToRun.Count
    if ($skippedCount -gt 0) {
        Write-Host "Skipping $skippedCount embedding model(s) due to -SkipEmbeddings." -ForegroundColor DarkYellow
    }

    if (-not $modelsToRun -or $modelsToRun.Count -eq 0) {
        throw "All selected models were filtered out by -SkipEmbeddings."
    }
}

if (-not $DisableJudge -and [string]::IsNullOrWhiteSpace($JudgeModel)) {
    if ($modelsToRun -contains "qwen3:30b") {
        $JudgeModel = "qwen3:30b"
    }
    elseif ($modelsToRun -contains "qwen3-coder:30b") {
        $JudgeModel = "qwen3-coder:30b"
    }
}

$tests = @(
    [PSCustomObject]@{
        Name = "coding"
    Weight = 0.25
        Temperature = 0.2
        Prompt = @"
Write a Python function `fibonacci(n: int) -> int`.
Requirements:
- iterative approach only
- raise ValueError for negative n
- return 0 for n=0 and 1 for n=1
- no recursion
- output only valid Python code
"@
    },
    [PSCustomObject]@{
        Name = "reasoning"
        Weight = 0.15
        Temperature = 0.2
        Prompt = @"
Solve this and output only one line in the exact format ANSWER=<number>.

A founder has 3 launch channels. Channel A generated 8 signups/day for 3 days.
Channel B generated 6 signups/day for 4 days.
Channel C generated 2 signups/day for 3 days.
If 12 signups churned in week one,
How many retained signups remain?
"@
    },
    [PSCustomObject]@{
        Name = "instruction_following"
        Weight = 0.15
        Temperature = 0.1
        Prompt = @"
Return strict JSON only (no markdown, no extra keys) with this exact schema:
{
  "title": string,
  "tags": [string, string, string],
  "word_count": integer
}

Use title = "AI Co-Founder Weekly Brief", tags relevant to startup growth, and word_count = 220.
"@
    },
    [PSCustomObject]@{
        Name = "creative_writing"
        Weight = 0.20
        Temperature = 0.9
        Prompt = @"
Write a blog post opening for startup founders about using an AI co-founder in early-stage product development.
Constraints:
- 180 to 240 words
- exactly one rhetorical question
- include the exact phrase "AI co-founder"
- include one vivid metaphor
- end with a CTA sentence that starts with "Try"
- tone: confident, practical, non-hype
"@
    },
    [PSCustomObject]@{
        Name = "creative_longform"
        Weight = 0.25
        Temperature = 0.85
        Prompt = @"
Write a markdown section for a developer-facing startup blog post about using an AI co-founder to ship product updates faster.
Constraints:
- 380 to 560 words
- include at least 2 H2 headings using markdown syntax (## Heading)
- include at least 3 bullet points
- include one founder-grounded anecdote
- include one concrete example experiment with measurable outcome
- include practical steps readers can apply this week
- tone: practical, grounded, no hype
"@
    }
)

$rows = @()
$runTimestamp = (Get-Date).ToString("s")
$runId = (Get-Date).ToString("yyyyMMdd-HHmmss")

Write-Host "Running quality shootout for $($modelsToRun.Count) models..." -ForegroundColor Cyan
if (-not $DisableJudge -and -not [string]::IsNullOrWhiteSpace($JudgeModel)) {
    Write-Host "Judge model: $JudgeModel" -ForegroundColor DarkCyan
}
else {
    Write-Host "Judge model disabled" -ForegroundColor DarkCyan
}

foreach ($model in $modelsToRun) {
    Write-Host "`n=== Evaluating $model ===" -ForegroundColor Cyan
    $meta = $metadataMap[$model]
    $scores = @{}
    $judgeScores = @{}
    $notes = @()
    $failed = $false

    foreach ($test in $tests) {
        Write-Host "  Task: $($test.Name)..." -NoNewline
        $attempt = 0
        $maxAttempts = $MaxRetriesPerTask + 1
        $taskCompleted = $false

        while (-not $taskCompleted -and $attempt -lt $maxAttempts) {
            $attempt += 1
            try {
                $promptForAttempt = if ($attempt -eq 1) {
                    $test.Prompt
                }
                else {
                    @"
Retry attempt $attempt of $maxAttempts.
You previously failed at least one required constraint.
Follow every requirement exactly and return only the requested output format.

$($test.Prompt)
"@
                }

                $resp = Invoke-OllamaGenerate -Model $model -Prompt $promptForAttempt -Temperature $test.Temperature -NumCtx 4096 -NumGpu 99 -Timeout $TimeoutSec
                $text = if ($null -ne $resp.response) { [string]$resp.response } else { "" }

                switch ($test.Name) {
                    "coding" { $result = Score-Coding -Text $text }
                    "reasoning" { $result = Score-Reasoning -Text $text }
                    "instruction_following" { $result = Score-InstructionFollowing -Text $text }
                    "creative_writing" { $result = Score-CreativeWriting -Text $text }
                    "creative_longform" { $result = Score-CreativeLongform -Text $text }
                }

                $shouldRetry = $false
                if ($RetryOnZeroScore -and $result.Score -eq 0 -and $attempt -lt $maxAttempts) {
                    $shouldRetry = $true
                }
                if ([string]::IsNullOrWhiteSpace($text) -and $attempt -lt $maxAttempts) {
                    $shouldRetry = $true
                }

                if ($shouldRetry) {
                    $notes += "$($test.Name): attempt $attempt scored 0/10 or empty output, retrying"
                    continue
                }

                $scores[$test.Name] = $result.Score
                if ($attempt -gt 1) {
                    $notes += "$($test.Name): recovered on attempt $attempt/$maxAttempts"
                }
                $notes += "$($test.Name): $($result.Notes)"

                if (-not $DisableJudge -and -not [string]::IsNullOrWhiteSpace($JudgeModel) -and $JudgeModel -ne $model) {
                    $judge = Get-JudgeScore -Judge $JudgeModel -TargetModel $model -TaskName $test.Name -Prompt $test.Prompt -Response $text -Timeout $TimeoutSec
                    if ($null -ne $judge.Score) {
                        $judgeScores[$test.Name] = $judge.Score
                        $notes += "$($test.Name)_judge: $($judge.Score)/10 ($($judge.Reason))"
                    }
                    else {
                        $notes += "$($test.Name)_judge: unavailable"
                    }
                }

                Write-Host " $($result.Score)/10"
                $taskCompleted = $true
            }
            catch {
                if ($attempt -lt $maxAttempts) {
                    $notes += "$($test.Name): attempt $attempt failed ($($_.Exception.Message)), retrying"
                }
                else {
                    $failed = $true
                    $scores[$test.Name] = 0
                    $notes += "$($test.Name): FAILED after $attempt attempt(s) - $($_.Exception.Message)"
                    Write-Host " failed" -ForegroundColor Yellow
                    $taskCompleted = $true
                }
            }
        }
    }

    $weightedHeuristic = 0.0
    foreach ($test in $tests) {
        $weightedHeuristic += (($scores[$test.Name] / 10.0) * $test.Weight)
    }

    $judgeAverage = if ($judgeScores.Count -gt 0) {
        [math]::Round((($judgeScores.Values | Measure-Object -Average).Average), 2)
    }
    else { $null }

    $overall = if ($null -ne $judgeAverage) {
        # Blend deterministic checks with qualitative judging
        [math]::Round((($weightedHeuristic * 10.0) * 0.65) + ($judgeAverage * 0.35), 2)
    }
    else {
        [math]::Round($weightedHeuristic * 10.0, 2)
    }

    $rows += [PSCustomObject]@{
        RunTimestamp               = $runTimestamp
        RunId                      = $runId
        Model                      = $model
        Family                     = if ($meta) { $meta.Family } else { "Unknown" }
        Parameter                  = if ($meta) { $meta.Parameter } else { "Unknown" }
        Quantization               = if ($meta) { $meta.Quantization } else { "Unknown" }
        "ModelSize(GiB)"           = if ($meta) { $meta.SizeGiB } else { $null }
        CodingScore                = $scores["coding"]
        ReasoningScore             = $scores["reasoning"]
        InstructionScore           = $scores["instruction_following"]
        CreativeWritingScore       = $scores["creative_writing"]
        CreativeLongformScore      = $scores["creative_longform"]
        CreativeCombinedScore      = [math]::Round((($scores["creative_writing"] + $scores["creative_longform"]) / 2.0), 2)
        JudgeAverage               = $judgeAverage
        OverallQualityScore        = $overall
        Status                     = if ($failed) { "PARTIAL" } else { "OK" }
        Notes                      = ($notes -join " | ")
    }

    Write-Host "  Overall quality: $overall/10" -ForegroundColor Green
}

$ranked = $rows | Sort-Object OverallQualityScore -Descending

$repoRoot = Split-Path $PSScriptRoot -Parent
$tmpDir = Join-Path $repoRoot ".tmp"
if (-not (Test-Path $tmpDir)) {
    New-Item -Path $tmpDir -ItemType Directory | Out-Null
}


$csvLatestScriptPath = Join-Path $PSScriptRoot "model_quality_shootout.csv"
$csvLatestTmpPath = Join-Path $tmpDir "model_quality_shootout.csv"
$csvArchiveScriptPath = Join-Path $PSScriptRoot "model_quality_shootout_archive.csv"
$csvArchiveTmpPath = Join-Path $tmpDir "model_quality_shootout_archive.csv"

$summaryLatestPath = Join-Path $tmpDir "model_quality_shootout_summary.txt"
$summaryArchivePath = Join-Path $tmpDir "model_quality_shootout_summary_archive.txt"

$snapshotDir = Join-Path $tmpDir "model_quality_shootout_runs"
if (-not (Test-Path $snapshotDir)) {
    New-Item -Path $snapshotDir -ItemType Directory | Out-Null
}

$csvSnapshotPath = Join-Path $snapshotDir ("model_quality_shootout_{0}.csv" -f $runId)
$summarySnapshotPath = Join-Path $snapshotDir ("model_quality_shootout_summary_{0}.txt" -f $runId)

function Write-ResultsCsv {
    param(
        [array]$Data,
        [string]$Path
    )

    function Ensure-ResultsCsvSchema {
        param(
            [string]$CsvPath,
            [array]$IncomingData
        )

        if (-not (Test-Path $CsvPath)) {
            return
        }

        $existing = Import-Csv $CsvPath
        if (-not $existing -or $existing.Count -eq 0) {
            return
        }

        $requiredCols = @("RunTimestamp", "RunId")
        $existingCols = @($existing[0].PSObject.Properties.Name)
        $missingCols = @($requiredCols | Where-Object { $existingCols -notcontains $_ })

        if ($missingCols.Count -eq 0) {
            return
        }

        $targetCols = @($IncomingData[0].PSObject.Properties.Name)
        $rewritten = foreach ($row in $existing) {
            $obj = [ordered]@{}
            foreach ($col in $targetCols) {
                if ($row.PSObject.Properties.Name -contains $col) {
                    $obj[$col] = $row.$col
                }
                else {
                    $obj[$col] = ""
                }
            }
            [PSCustomObject]$obj
        }

        $rewritten | Export-Csv -Path $CsvPath -NoTypeInformation
    }

    Ensure-ResultsCsvSchema -CsvPath $Path -IncomingData $Data

    if (Test-Path $Path) {
        $Data | Export-Csv -Path $Path -NoTypeInformation -Append -Force
    }
    else {
        $Data | Export-Csv -Path $Path -NoTypeInformation
    }
}

function Write-LatestCsv {
    param(
        [array]$Data,
        [string]$Path
    )
    $Data | Export-Csv -Path $Path -NoTypeInformation
}

function Rotate-Snapshots {
    param(
        [string]$Directory,
        [int]$Keep
    )

    $csvSnapshots = Get-ChildItem -Path $Directory -Filter "model_quality_shootout_*.csv" | Sort-Object LastWriteTime -Descending
    $summarySnapshots = Get-ChildItem -Path $Directory -Filter "model_quality_shootout_summary_*.txt" | Sort-Object LastWriteTime -Descending

    if ($csvSnapshots.Count -gt $Keep) {
        $csvSnapshots | Select-Object -Skip $Keep | Remove-Item -Force
    }
    if ($summarySnapshots.Count -gt $Keep) {
        $summarySnapshots | Select-Object -Skip $Keep | Remove-Item -Force
    }
}

# Latest outputs are overwritten each run.
Write-LatestCsv -Data $ranked -Path $csvLatestScriptPath
Write-LatestCsv -Data $ranked -Path $csvLatestTmpPath

# Archive outputs are append-only for longitudinal tracking.
Write-ResultsCsv -Data $ranked -Path $csvArchiveScriptPath
Write-ResultsCsv -Data $ranked -Path $csvArchiveTmpPath

# Per-run snapshots in .tmp are rotated.
Write-LatestCsv -Data $ranked -Path $csvSnapshotPath

$topCreative = $ranked | Sort-Object CreativeCombinedScore -Descending | Select-Object -First 5
$topCoding = $ranked | Sort-Object CodingScore -Descending | Select-Object -First 5

$summary = @(
    "Model Quality Shootout"
    "Generated: $(Get-Date -Format s)"
    "RunId: $runId"
    "Models tested: $($modelsToRun.Count)"
    "Judge model: $(if ($DisableJudge -or [string]::IsNullOrWhiteSpace($JudgeModel)) { 'Disabled' } else { $JudgeModel })"
    "",
    "Top Overall"
)

foreach ($item in ($ranked | Select-Object -First 10)) {
    $summary += ("- {0} | Overall {1}/10 | CreativeCombined {2}/10 | Coding {3}/10 | {4} GiB" -f $item.Model, $item.OverallQualityScore, $item.CreativeCombinedScore, $item.CodingScore, $item."ModelSize(GiB)")
}

$summary += ""
$summary += "Top Creative Writing"
foreach ($item in $topCreative) {
    $summary += ("- {0} | CreativeCombined {1}/10 | Intro {2}/10 | Longform {3}/10 | Overall {4}/10" -f $item.Model, $item.CreativeCombinedScore, $item.CreativeWritingScore, $item.CreativeLongformScore, $item.OverallQualityScore)
}

$summary += ""
$summary += "Top Coding"
foreach ($item in $topCoding) {
    $summary += ("- {0} | Coding {1}/10 | Overall {2}/10" -f $item.Model, $item.CodingScore, $item.OverallQualityScore)
}

$summary | Set-Content -Path $summaryLatestPath
$summary | Set-Content -Path $summarySnapshotPath

$summaryArchiveBlock = @(
    ""
    "===== Run $runId ($runTimestamp) ====="
) + $summary

$summaryArchiveBlock | Add-Content -Path $summaryArchivePath

Rotate-Snapshots -Directory $snapshotDir -Keep $SnapshotRetention

Write-Host "`n=== Quality Shootout Results ===" -ForegroundColor Cyan
$ranked | Format-Table Model, "ModelSize(GiB)", CodingScore, ReasoningScore, InstructionScore, CreativeWritingScore, CreativeLongformScore, CreativeCombinedScore, JudgeAverage, OverallQualityScore, Status -AutoSize

Write-Host "`nSaved latest CSV: $csvLatestScriptPath"
Write-Host "Saved latest CSV: $csvLatestTmpPath"
Write-Host "Saved archive CSV: $csvArchiveScriptPath"
Write-Host "Saved archive CSV: $csvArchiveTmpPath"
Write-Host "Saved latest summary: $summaryLatestPath"
Write-Host "Saved archive summary: $summaryArchivePath"
Write-Host "Saved run snapshot CSV: $csvSnapshotPath"
Write-Host "Saved run snapshot summary: $summarySnapshotPath"
