$repo = 'C:\Users\Cho-zen\SEO-scan-Pro'
$tracked = 'main'
$branchNow = (git -C $repo rev-parse --abbrev-ref HEAD 2>$null).Trim()
$head = (git -C $repo rev-parse HEAD 2>$null).Trim()
$porcelain = git -C $repo status --porcelain 2>$null
$dirtyCount = @($porcelain).Count
git -C $repo fetch origin --quiet 2>$null | Out-Null
$fetchOk = ($LASTEXITCODE -eq 0)
$up = (git -C $repo rev-parse --verify "origin/$branchNow" 2>$null)
$up = if ($up) { $up.Trim() } else { $null }
$ancestor = $null
$upAhead = $false
if ($up -and $head -ne $up) {
  git -C $repo merge-base --is-ancestor $head $up 2>$null
  $ancestor = ($LASTEXITCODE -eq 0)
  $upAhead = $true
}
@{name='seoscan-pro'; tracked=$tracked; branch=$branchNow; head=$head; dirty=$dirtyCount; fetchOk=$fetchOk; upstream=$up; upAhead=$upAhead; ancestor=$ancestor} | ConvertTo-Json -Compress
