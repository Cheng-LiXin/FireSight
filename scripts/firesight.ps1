<#
.SYNOPSIS
    FireSight 系统一键启动 / 关闭。

.DESCRIPTION
    启动：拉起 FastAPI（默认 8000，同时托管 apps/web/dist 里的控制台前端），
          等它真的能响应再报 URL；可选同时起 Vite dev server（-Dev，5173 热更新）。
    关闭：找到监听端口的进程，**连同它的整棵进程树**一起结束，并复查端口已释放。

    为什么必须杀整棵树（这个项目踩过两次）：
      · .venv\Scripts\python.exe 是个**转发器**（266 KB），它再去起真正的解释器，
        所以「杀父进程」不会带走正在监听的那个子进程；
      · 从别处（IDE / 另一个终端 / 任务计划）起的服务，外壳没了子进程会留下，
        表现为「以为关了，其实还占着 8000」。
    所以这里先沿 python 链向上找到最顶层的那个进程，再 taskkill /F /T。

    启动不依赖 `fs-serve` 这个 console script：venv 可能是在它加进 pyproject 之前
    装的（那种情况下 .venv\Scripts 里只有改名前的 fs-web.exe，README 的排错一节
    就是这么写的）。改用 `python -m fire_sight.cli`，逻辑完全一样且永远可用。

.PARAMETER Action
    start / stop / restart / status（默认 status）

.PARAMETER Port
    服务端口，默认 8000

.PARAMETER Dev
    start 时同时起前端开发服务器（5173，热更新，/api 代理到本服务）

.PARAMETER Build
    start 前强制重建前端（npm run build）

.PARAMETER AutoBuild
    start 时只要检测到前端源码比 dist 新就自动重建（默认关闭）。
    启动.cmd 传的就是这个：桌面双击进来的路径不该要求人记得加 -Build，
    而漏重建的后果是「页面看起来没改」，排查起来最费时间。

.PARAMETER Foreground
    前台运行，Ctrl+C 即停（默认后台运行并把日志写进 .runtime/logs）

.PARAMETER Force
    stop 时不问确认（即使正在训练也直接停）

.EXAMPLE
    pwsh -File scripts\firesight.ps1 start
    pwsh -File scripts\firesight.ps1 stop
    pwsh -File scripts\firesight.ps1 restart -Dev
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status')]
    [string]$Action = 'status',

    [int]$Port = 8000,
    [switch]$Dev,
    [switch]$Build,
    [switch]$AutoBuild,
    [switch]$Foreground,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root '.venv\Scripts\python.exe'
$WebDir = Join-Path $Root 'apps\web'
$DistIndex = Join-Path $WebDir 'dist\index.html'
$LogDir = Join-Path $Root '.runtime\logs'
$DevPort = 5173

if (-not (Test-Path (Join-Path $Root 'pyproject.toml'))) {
    Write-Host "找不到项目根目录（期望 $Root 下有 pyproject.toml）" -ForegroundColor Red
    exit 1
}

# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------

function Write-Head([string]$Text) {
    Write-Host ''
    Write-Host "== $Text" -ForegroundColor Cyan
}

function Write-Ok([string]$Text) { Write-Host "  $Text" -ForegroundColor Green }
function Write-Warn([string]$Text) { Write-Host "  $Text" -ForegroundColor Yellow }
function Write-Bad([string]$Text) { Write-Host "  $Text" -ForegroundColor Red }

function Shorten([string]$Text, [int]$Max = 96) {
    if (-not $Text) { return '' }
    $t = $Text.Trim()
    if ($t.Length -le $Max) { return $t }
    return $t.Substring(0, $Max - 1) + '…'
}

function Get-ListeningPids([int]$P) {
    @(Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
}

function Get-ProcInfo([int]$ProcessId) {
    Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
}

<#
    沿 python 解释器链向上找到最顶层的那个进程。
    只爬 python 进程，避免把外层的 pwsh / explorer（甚至别人的宿主）卷进来。
#>
function Get-TreeTop([int]$ProcessId) {
    $top = $ProcessId
    $cur = $ProcessId
    for ($i = 0; $i -lt 4; $i++) {
        $p = Get-ProcInfo $cur
        if (-not $p) { break }
        $parent = Get-ProcInfo $p.ParentProcessId
        if (-not $parent) { break }
        if ($parent.Name -notmatch '^python') { break }
        $top = [int]$parent.ProcessId
        $cur = [int]$parent.ProcessId
    }
    return $top
}

function Get-Api([string]$Path, [int]$TimeoutSec = 4) {
    try {
        return Invoke-RestMethod -Uri "http://127.0.0.1:$Port$Path" -TimeoutSec $TimeoutSec
    } catch {
        return $null
    }
}

function Test-Serving {
    return [bool](Get-Api '/openapi.json' 2)
}

function Get-ActiveTraining {
    $jobs = Get-Api '/api/train/jobs' 5
    if (-not $jobs) { return $null }
    @($jobs | Where-Object { $_.status -eq 'running' -or $_.status -eq 'stopping' }) | Select-Object -First 1
}

<#
    后台起一个进程，**不继承本进程的任何标准句柄**。

    为什么不用 Start-Process：服务端会一直占着调用方的输出管道，于是
    `pwsh -File firesight.ps1 start | Select-Object -Last 3`（以及 Tee-Object、
    CI 里采集输出）**永远等不到 EOF**——脚本早就跑完了，管道却不关。实测踩到过。

    WMI 创建的进程挂在 WmiPrvSE 下，没有控制台也不继承句柄，所以管道随脚本退出
    立刻关闭，服务照常留在后台。返回值是 cmd.exe 的 PID（重定向要经它解释），
    起不来时返回 0。
#>
function Start-Detached([string]$Exe, [string[]]$CliArgs, [string]$OutLog, [string]$ErrLog) {
    $inner = '"' + $Exe + '" ' + ($CliArgs -join ' ') + ' > "' + $OutLog + '" 2> "' + $ErrLog + '"'
    $line = 'cmd.exe /c "' + $inner + '"'
    try {
        $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
            CommandLine      = $line
            CurrentDirectory = $Root
        } -ErrorAction Stop
        if ($r -and $r.ReturnValue -eq 0) { return [int]$r.ProcessId }
    } catch {
        Write-Host "      （WMI 起进程失败，退回 Start-Process：$($_.Exception.Message)）" -ForegroundColor DarkGray
    }
    $p = Start-Process -FilePath $Exe -ArgumentList $CliArgs -WorkingDirectory $Root `
        -WindowStyle Hidden -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -PassThru
    return [int]$p.Id
}

# --------------------------------------------------------------------------
# 结束某个端口上的服务（含整棵进程树）
# --------------------------------------------------------------------------

function Stop-Endpoint([int]$P, [string]$Label) {
    $pids = Get-ListeningPids $P
    if ($pids.Count -eq 0) {
        Write-Host "  $Label（$P）：没有在监听"
        return $true
    }

    foreach ($lp in $pids) {
        $top = Get-TreeTop $lp
        $info = Get-ProcInfo $top
        $line = if ($info) { Shorten $info.CommandLine } else { '（进程已退出）' }
        Write-Host "  结束 $Label：PID $top"
        Write-Host "       $line" -ForegroundColor DarkGray
        if ($top -ne $lp) {
            Write-Host "       （监听者是 PID $lp，它是 PID $top 的子进程——转发器链）" -ForegroundColor DarkGray
        }
        & taskkill /F /T /PID $top 2>&1 | Out-Null
    }

    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 400
        if ((Get-ListeningPids $P).Count -eq 0) {
            Write-Ok "$Label（$P）：已停止，端口已释放"
            return $true
        }
    }
    Write-Bad "$Label（$P）：端口仍被占用（PID $((Get-ListeningPids $P) -join ','))——可能有别的进程在抢"
    return $false
}

# --------------------------------------------------------------------------
# 前端构建检查
# --------------------------------------------------------------------------

function Invoke-Build {
    Write-Head '构建前端（npm run build）'
    if (-not (Test-Path $WebDir)) {
        Write-Bad "找不到 $WebDir"
        return $false
    }
    Push-Location $WebDir
    try {
        & npm run build
        if ($LASTEXITCODE -ne 0) {
            Write-Bad "构建失败（退出码 $LASTEXITCODE）"
            return $false
        }
    } finally {
        Pop-Location
    }
    Write-Ok '构建完成'
    return $true
}

function Test-DistStale {
    <#
        返回 'missing' / 'stale' / 'ok'。

        判据是「有没有比产物更晚的输入文件」，比较对象必须覆盖**所有**进入
        打包的输入，少一类就会出现「我改了它，页面却没变」：
          · src/          —— 组件与样式
          · index.html    —— Vite 的 HTML 入口，标题与 favicon 都在里面
          · public/       —— 原样拷贝，不参与 hash 比对
          · .env*         —— 会被 vite 读进去注入 define
          · 根配置文件    —— vite / package / tsconfig
    #>
    if (-not (Test-Path $DistIndex)) { return 'missing' }
    $built = (Get-Item $DistIndex).LastWriteTime

    $candidates = @()

    $candidates += Get-ChildItem (Join-Path $WebDir 'src') -Recurse -File -ErrorAction SilentlyContinue

    $pub = Join-Path $WebDir 'public'
    if (Test-Path $pub) {
        $candidates += Get-ChildItem $pub -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notmatch '[\\/](node_modules|dist)[\\/]' }
    }

    $candidates += Get-ChildItem $WebDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^(vite\.|package\.json$|tsconfig|index\.html$)' -or $_.Name -like '.env*' }

    $newest = $candidates |
        Where-Object { $_.FullName -ne $DistIndex } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

    if ($newest -and $newest.LastWriteTime -gt $built) { return 'stale' }
    return 'ok'
}

# --------------------------------------------------------------------------
# start
# --------------------------------------------------------------------------

function Invoke-Start {
    Write-Head 'FireSight 启动'

    if (-not (Test-Path $Py)) {
        Write-Bad "找不到虚拟环境的解释器：$Py"
        Write-Host '  先创建并安装：python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e ".\apps\fire-sight[web]" -e .\apps\platform'
        return 1
    }

    $pids = Get-ListeningPids $Port
    if ($pids.Count -gt 0) {
        $top = Get-TreeTop $pids[0]
        $info = Get-ProcInfo $top
        Write-Warn "端口 $Port 已被占用（PID $top）：$(Shorten $info.CommandLine)"
        Write-Host "  要接管就先关闭：pwsh -File scripts\firesight.ps1 stop" -ForegroundColor DarkGray
        if (Test-Serving) {
            $v = (Get-Api '/openapi.json' 2).info.version
            Write-Ok "服务其实已经在跑（v$v）→ http://127.0.0.1:$Port"
            return 0
        }
        return 1
    }

    # 前端产物
    $state = Test-DistStale
    if ($Build -or $AutoBuild) {
        if ($state -eq 'ok') {
            Write-Ok '前端产物是最新的'
        } elseif ($state -eq 'missing') {
            if (-not $Build) { Write-Warn 'apps/web/dist 不存在——没有它就没有控制台界面，先构建' }
        }
        if (-not (Invoke-Build)) { return 1 }
    } elseif ($state -eq 'missing') {
        Write-Warn 'apps/web/dist 不存在——没有它就没有控制台界面，先构建'
        if (-not (Invoke-Build)) { return 1 }
    } elseif ($state -eq 'stale') {
        Write-Warn '前端源码比 dist 新，页面可能不是最新的：'
        Write-Host '       重建：pwsh -File scripts\firesight.ps1 start -Build' -ForegroundColor DarkGray
    } else {
        Write-Ok '前端产物是最新的'
    }

    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    Write-Head "启动服务（127.0.0.1:$Port）"
    $pyArgs = @('-m', 'fire_sight.cli', '--host', '127.0.0.1', '--port', "$Port")

    if ($Foreground) {
        Write-Host '  前台运行，Ctrl+C 停止' -ForegroundColor DarkGray
        Push-Location $Root
        try {
            & $Py @pyArgs
        } finally {
            Pop-Location
        }
        return 0
    }

    $ts = Get-Date -Format 'yyyyMMdd-HHmmss'
    $outLog = Join-Path $LogDir "serve-$ts.out.log"
    $errLog = Join-Path $LogDir "serve-$ts.err.log"
    $pid0 = Start-Detached $Py $pyArgs $outLog $errLog
    if ($pid0 -le 0) {
        Write-Bad '起进程失败'
        return 1
    }
    Write-Host "  已拉起（PID $pid0），日志：.runtime\logs\serve-$ts.*.log" -ForegroundColor DarkGray

    $version = $null
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        $spec = Get-Api '/openapi.json' 2
        if ($spec) { $version = $spec.info.version; break }
        if (-not (Get-Process -Id $pid0 -ErrorAction SilentlyContinue)) {
            Write-Bad '服务进程已退出，看日志：'
            Write-Host "       $errLog" -ForegroundColor DarkGray
            if (Test-Path $errLog) { Get-Content $errLog -Tail 15 | ForEach-Object { Write-Host "       $_" -ForegroundColor DarkGray } }
            return 1
        }
    }

    if (-not $version) {
        Write-Bad '等了 30 秒还没起来，看日志：'
        Write-Host "       $errLog" -ForegroundColor DarkGray
        return 1
    }

    Write-Ok "服务已就绪（v$version）"
    Write-Host "      控制台  http://127.0.0.1:$Port" -ForegroundColor White
    Write-Host "      接口文档 http://127.0.0.1:$Port/docs" -ForegroundColor DarkGray

    if ($Dev) {
        Write-Head "启动前端开发服务器（$DevPort，热更新）"
        $devOut = Join-Path $LogDir "vite-$ts.out.log"
        $devErr = Join-Path $LogDir "vite-$ts.err.log"
        [void](Start-Detached 'npm.cmd' @('run', 'dev') $devOut $devErr)
        for ($i = 0; $i -lt 40; $i++) {
            Start-Sleep -Milliseconds 500
            if ((Get-ListeningPids $DevPort).Count -gt 0) { break }
        }
        if ((Get-ListeningPids $DevPort).Count -gt 0) {
            Write-Ok "开发模式已就绪  http://localhost:$DevPort（改前端源码即时生效）"
        } else {
            Write-Warn "Vite 没起来，看日志：$devErr"
        }
    }

    Write-Host ''
    Write-Host "  关闭：pwsh -File scripts\firesight.ps1 stop" -ForegroundColor DarkGray
    return 0
}

# --------------------------------------------------------------------------
# stop
# --------------------------------------------------------------------------

function Invoke-Stop {
    Write-Head 'FireSight 关闭'

    $training = Get-ActiveTraining
    if ($training) {
        Write-Warn "有训练在跑：$($training.dataset)（任务 $($training.id)，$($training.epochs) 轮）"
        Write-Host '       关掉服务会中断它。现场与检查点会留下，下次可「继续训练」接上（v1.7.0）。' -ForegroundColor Yellow
        if (-not $Force) {
            $ans = Read-Host '       仍然关闭？[y/N]'
            if ($ans -notmatch '^[Yy]') {
                Write-Host '  已取消，服务继续运行'
                return 2
            }
        }
    }

    $ok = $true
    if (-not (Stop-Endpoint $Port '服务')) { $ok = $false }
    [void](Stop-Endpoint $DevPort '前端 dev')

    if ($ok) {
        Write-Host ''
        Write-Ok '已全部关闭'
        return 0
    }
    return 1
}

# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

function Invoke-Status {
    Write-Head 'FireSight 状态'

    $pids = Get-ListeningPids $Port
    if ($pids.Count -gt 0) {
        $top = Get-TreeTop $pids[0]
        $spec = Get-Api '/openapi.json' 3
        $ver = if ($spec) { "v$($spec.info.version)" } else { '（端口在监听但接口没响应）' }
        Write-Ok "服务：运行中 $ver   http://127.0.0.1:$Port   PID $top"
    } else {
        Write-Host '  服务：未运行'
    }

    $devPids = Get-ListeningPids $DevPort
    if ($devPids.Count -gt 0) {
        Write-Host "  前端 dev：运行中   http://localhost:$DevPort   PID $($devPids[0])"
    }

    if ($pids.Count -gt 0) {
        $active = Get-Api '/api/models/active' 3
        if ($active) { Write-Host "  当前权重：$($active.run_id)  （$($active.source)）" }

        $training = Get-ActiveTraining
        if ($training) {
            Write-Warn "训练：$($training.dataset) 正在跑（任务 $($training.id)）"
        } else {
            Write-Host '  训练：空闲'
        }

        $runs = Get-Api '/api/train/runs' 5
        if ($runs) {
            $resumable = @($runs | Where-Object { $_.resumable })
            Write-Host "  训练记录：$(@($runs).Count) 条，其中可续训 $($resumable.Count) 条"
        }
    }

    $distState = Test-DistStale
    switch ($distState) {
        'ok' { Write-Host '  前端产物：最新' }
        'stale' { Write-Warn '前端产物：源码更新了但没重建（双击 启动.cmd 自动重建，命令行用 start -Build）' }
        'missing' { Write-Warn '前端产物：不存在（start 会自动构建）' }
    }

    # 先问有没有这个命令。脚本第 63 行设了 $ErrorActionPreference='Stop'，
    # 而缺失的原生命令抛的是 CommandNotFoundException——`2>$null` 抑制不住它，
    # 于是在没有 nvidia-smi 的机器上 status 直接崩（开发的这台有 RTX 4060，
    # 所以本机从不暴露；换台展示机才炸）。2026-10-09 代码审查 R15。
    $gpu = $null
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        $gpu = & nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader 2>$null
    } else {
        Write-Host '  GPU：未找到 nvidia-smi（按 CPU 运行）' -ForegroundColor DarkGray
    }
    if ($gpu) {
        Write-Host "  GPU：$gpu"
        # 守卫是按**总量**算预算的（不是按可用量），所以别人占着显存时这里必须提醒：
        # 否则「预算够」的判定会让训练起来之后 OOM。
        $usedMatch = [regex]::Match($gpu, '(\d+)\s*MiB,\s*(\d+)\s*MiB')
        if ($usedMatch.Success -and -not $training) {
            $used = [int]$usedMatch.Groups[1].Value
            $total = [int]$usedMatch.Groups[2].Value
            if ($used -gt 2000 -and ($used / $total) -gt 0.35) {
                Write-Warn "显存已用 $([math]::Round(100 * $used / $total))% 而本项目没在训练"
                Write-Host '       守卫按「总量 − 固定开销」算预算，不看别人占了多少——这时起训练会 OOM。' -ForegroundColor DarkGray
                Write-Host '       查占用者：nvidia-smi --query-compute-apps=pid,process_name --format=csv' -ForegroundColor DarkGray
            }
        }
    }

    $logs = Get-ChildItem $LogDir -Filter 'serve-*.err.log' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($logs) { Write-Host "  最近日志：$($logs.FullName.Replace($Root + '\', ''))" -ForegroundColor DarkGray }

    return 0
}

# --------------------------------------------------------------------------

switch ($Action) {
    'start' { exit (Invoke-Start) }
    'stop' { exit (Invoke-Stop) }
    'status' { exit (Invoke-Status) }
    'restart' {
        $r = Invoke-Stop
        if ($r -eq 2) { exit 2 }
        Start-Sleep -Milliseconds 800
        exit (Invoke-Start)
    }
}
