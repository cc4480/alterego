$apps = @(
  @{name='secscan'; repo='C:\Users\Cho-zen\VibeScan-Enterprise-Build'},
  @{name='seoscan-pro'; repo='C:\Users\Cho-zen\SEO-scan-Pro'},
  @{name='complyscan'; repo='C:\Users\Cho-zen\complyscan'},
  @{name='seclayer-app'; repo='C:\Users\Cho-zen\seclayer.io2026'}
)
$results = @()
foreach ($a in $apps) {
  $repo = $a.repo
  $r = @{name=$a.name; repo=$repo}
  $r.branch = ((git -C $repo rev-parse --abbrev-ref HEAD 2>$null) -join '').Trim()
  $r.head = ((git -C $repo rev-parse HEAD 2>$null) -join '').Trim()
  $r.gitOk = [bool]$r.head
  $r.dirty = @((git -C $repo status --porcelain 2>$null)).Count
  git -C $repo fetch origin --quiet 2>$null | Out-Null
  $r.fetchOk = ($LASTEXITCODE -eq 0)
  $up = ((git -C $repo rev-parse --verify "origin/$($r.branch)" 2>$null) -join '').Trim()
  $r.upstream = if ($up) { $up } else { $null }
  $r.pulled = $false; $r.diverged = $false; $r.subjects = @(); $r.pullOk = $null
  if ($r.upstream -and $r.upstream -ne $r.head -and $r.dirty -eq 0 -and $r.gitOk) {
    git -C $repo merge-base --is-ancestor HEAD "origin/$($r.branch)" 2>$null | Out-Null
    $anc = ($LASTEXITCODE -eq 0)
    if ($anc) {
      $old = $r.head
      $po = ((git -C $repo pull --ff-only origin $r.branch 2>&1) -join "`n")
      $r.pullOk = ($LASTEXITCODE -eq 0)
      $new = ((git -C $repo rev-parse HEAD 2>$null) -join '').Trim()
      if ($new -and $new -ne $old) {
        $r.pulled = $true
        $r.head = $new
        $r.subjects = @(git -C $repo log --format=%s "$old..$new" 2>$null)
      }
      $r.pullNote = $po.Substring(0, [Math]::Min(300, $po.Length))
    } else { $r.diverged = $true }
  }
  $results += $r
}
$results | ConvertTo-Json -Depth 5 -Compress
