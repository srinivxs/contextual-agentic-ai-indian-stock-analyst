# Shared by demo-up.ps1 and demo-down.ps1. Dot-sourced, never run on its own.
#
# Written for Windows PowerShell 5.1 as well as PowerShell 7: no `&&`, no ternaries, and ECS network
# settings in the AWS CLI's shorthand syntax, because 5.1 strips the double quotes out of JSON passed
# to a native program.

$DemoRepo = Split-Path $PSScriptRoot -Parent
$DemoProfile = 'stock-analyst-admin'
$DemoRegion = 'ap-south-1'
$DemoCluster = 'stock-analyst-demo'
$DemoService = 'stock-analyst-demo-api'
$DemoUrl = $null

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $Text) -ForegroundColor Cyan
}

function Invoke-Aws([string[]]$Arguments) {
    $output = & aws @Arguments
    if ($LASTEXITCODE -ne 0) { throw "aws $($Arguments[0..1] -join ' ') failed (exit $LASTEXITCODE)." }
    return $output
}

# Only ever infra/stack or infra/edge: the two roots a demo session changes. bootstrap and cicd are
# applied once, by hand, and no script touches them.
function Invoke-Terraform([ValidateSet('stack', 'edge')][string]$Root, [string[]]$Arguments) {
    $directory = Join-Path $DemoRepo "infra\$Root"
    & terraform "-chdir=$directory" @Arguments
    if ($LASTEXITCODE -ne 0) { throw "terraform $($Arguments[0]) in infra/$Root failed or was cancelled." }
}

# Profile, region, account and the two Google values Terraform needs, from the git-ignored .env.
# Nothing read here is ever printed.
function Initialize-DemoSession {
    $env:AWS_PROFILE = $DemoProfile
    $env:AWS_REGION = $DemoRegion
    $env:AWS_DEFAULT_REGION = $DemoRegion

    $account = & aws sts get-caller-identity --query Account --output text
    if ($LASTEXITCODE -ne 0) {
        throw "The AWS session has expired. Run:  aws login --profile $DemoProfile   then run this again."
    }
    $env:TF_VAR_allowed_account_id = $account

    $dotenv = Join-Path $DemoRepo '.env'
    if (-not (Test-Path $dotenv)) { throw "$dotenv is missing: it holds the Google client settings." }
    foreach ($line in Get-Content $dotenv) {
        if ($line -match '^GOOGLE_CLIENT_ID=(.+)$') { $env:TF_VAR_google_client_id = $Matches[1].Trim() }
        if ($line -match '^GOOGLE_CLIENT_SECRET=(.+)$') { $env:TF_VAR_google_client_secret = $Matches[1].Trim() }
        if ($line -match '^ALLOWED_EMAILS=(.+)$') { $env:TF_VAR_allowed_emails = $Matches[1].Trim() }
    }
    if (-not $env:TF_VAR_google_client_id -or -not $env:TF_VAR_google_client_secret) {
        throw 'GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env.'
    }
    # Who may sign in. Google alone would let any account in, so the stack is never started without it.
    if (-not $env:TF_VAR_allowed_emails) {
        throw 'ALLOWED_EMAILS must be set in .env: the Google emails allowed to sign in, comma-separated.'
    }

    $script:DemoUrl = Invoke-Aws -Arguments @('ssm', 'get-parameter', '--name',
        '/stock-analyst/demo/public_base_url', '--query', 'Parameter.Value', '--output', 'text')
    Write-Host "Account ...$($account.Substring($account.Length - 4)), region $DemoRegion, $DemoUrl"
}

function Get-StackOutputs {
    $json = & terraform "-chdir=$(Join-Path $DemoRepo 'infra\stack')" output -json
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the stack outputs.' }
    $raw = ($json -join "`n") | ConvertFrom-Json
    return [pscustomobject]@{
        alb_dns_name           = $raw.alb_dns_name.value
        public_subnet_ids      = @($raw.public_subnet_ids.value)
        task_security_group_id = $raw.task_security_group_id.value
    }
}

# Run a one-off task (migrate or provision), wait for it to stop, and insist on exit code 0.
function Invoke-OneOffTask([string]$Family, [string]$Network) {
    Write-Host "  running $Family ..."
    $task = Invoke-Aws -Arguments @('ecs', 'run-task', '--cluster', $DemoCluster, '--task-definition', $Family,
        '--launch-type', 'FARGATE', '--network-configuration', $Network,
        '--query', 'tasks[0].taskArn', '--output', 'text')
    if (-not $task -or $task -eq 'None') { throw "$Family could not start (run-task returned no task)." }

    Invoke-Aws -Arguments @('ecs', 'wait', 'tasks-stopped', '--cluster', $DemoCluster, '--tasks', $task)
    $code = Invoke-Aws -Arguments @('ecs', 'describe-tasks', '--cluster', $DemoCluster, '--tasks', $task,
        '--query', 'tasks[0].containers[0].exitCode', '--output', 'text')
    if ($code -ne '0') {
        $group = '/stock-analyst/demo/' + ($Family -replace '^stock-analyst-demo-', '')
        throw "$Family exited with $code. Its log is in CloudWatch, log group $group."
    }
    Write-Host "  $Family finished (exit 0)"
}

# CloudFront can take a few minutes to pick up the new origin, so poll for up to about 8 minutes.
function Wait-DemoReady {
    $url = "$DemoUrl/api/readyz"
    for ($attempt = 1; $attempt -le 48; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10
            if ($response.StatusCode -eq 200 -and ($response.Content | ConvertFrom-Json).status -eq 'ready') {
                Write-Host "  $url is ready"
                return
            }
        } catch {
            Write-Host "  not yet ($($_.Exception.Message.Split([Environment]::NewLine)[0]))"
        }
        Start-Sleep -Seconds 10
    }
    throw "$url never reported ready. See 'When something goes wrong' in docs\runbook.md."
}

# After a destroy: the six things that bill by the hour or the month must all be gone.
function Test-NothingBillable {
    $checks = [ordered]@{
        'ECS clusters'           = @('ecs', 'list-clusters', '--query', 'clusterArns')
        'RDS instances'          = @('rds', 'describe-db-instances', '--query', 'DBInstances[].DBInstanceIdentifier')
        'load balancers'         = @('elbv2', 'describe-load-balancers', '--query', 'LoadBalancers[].LoadBalancerName')
        'non-default VPCs'       = @('ec2', 'describe-vpcs', '--filters', 'Name=isDefault,Values=false', '--query', 'Vpcs[].VpcId')
        'elastic IPs'            = @('ec2', 'describe-addresses', '--query', 'Addresses[].PublicIp')
        'manual RDS snapshots'   = @('rds', 'describe-db-snapshots', '--snapshot-type', 'manual', '--query', 'DBSnapshots[].DBSnapshotIdentifier')
    }
    $clean = $true
    foreach ($name in $checks.Keys) {
        $found = @((Invoke-Aws -Arguments ($checks[$name] + @('--output', 'text'))) | Where-Object { $_ -and $_ -ne 'None' })
        if ($found.Count -eq 0) {
            Write-Host "  none left: $name" -ForegroundColor Green
        } else {
            Write-Host "  STILL THERE: $name -> $($found -join ', ')" -ForegroundColor Red
            $clean = $false
        }
    }
    return $clean
}
