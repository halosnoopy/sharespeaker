$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot '../src/HubWindow.ps1'),[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw $errors[0] }
$names=@('Parse-Adapters','Render-Adapters','Start-Center','Start-OutputCenter','Start-BlueZ','Complete-AdapterSwitch','Reset-AdapterView','Start-HardwarePairing','Set-InputDraft','Show-InputPairing')
foreach ($node in $ast.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst]},$true)) {
    if ($node.Name -in $names) { . ([scriptblock]::Create($node.Extent.Text)) }
}
function Save-Settings {}
function Ensure-KeepAlive {}
function Usb-Executable {'usbipd.exe'}
function Show-Status {param($Text,[switch]$Show)}
function Start-ProcessJob {param($File,$ArgumentItems,$Done,$Label,$Timeout) $script:NextJob=@{File=$File; Args=$ArgumentItems; Done=$Done}}
function Start-Backend {param($Payload,$Label,$Timeout,$Then)
    if (-not $Payload.ContainsKey('controller')) {$Payload.controller=$Settings.Controller}
    if (-not $Payload.ContainsKey('usbId')) {$Payload.usbId=$Settings.UsbId}
    $script:NextBackend=@{Payload=$Payload; Then=$Then}
}
function Check($Condition,$Message) {if (-not $Condition) {throw $Message}}
function Complete-Job($Code=0,$Output='') {$job=$script:NextJob; $script:NextJob=$null; & $job.Done $Code $Output ''}
function Complete-Backend($Data) {$call=$script:NextBackend; $script:NextBackend=$null; if ($call.Then) {& $call.Then $Data}}
$Pending=$null; $Rendering=$false
$Settings=@{BusId='2-14'; UsbId='8087:0026'; Controller='02:00:00:00:00:31'; InputBusId=''; InputUsbId=''; InputController=''; Distro='Ubuntu'; Source='phone:02:00:00:00:00:10'; Outputs=@{speaker=@{Volume=58; Delay=391}}}
$controllerBox=New-Object System.Windows.Forms.TextBox
$adapterStateLabel=New-Object System.Windows.Forms.Label
$adapterCombo=New-Object System.Windows.Forms.ComboBox; $adapterCombo.DisplayMember='Label'
$inputAdapterCombo=New-Object System.Windows.Forms.ComboBox; $inputAdapterCombo.DisplayMember='Label'
$tabs=New-Object System.Windows.Forms.TabControl; [void]$tabs.TabPages.Add('play'); [void]$tabs.TabPages.Add('pair'); [void]$tabs.TabPages.Add('settings')
$inventory="2-14  8087:0026  Intel Bluetooth  Attached`n2-4  7392:c611  Edimax Bluetooth  Shared"
$adapters=@(Parse-Adapters $inventory); Render-Adapters $adapters; $adapterCombo.SelectedItem=$adapters[1]
Start-Center
Check ($NextJob.Args[0] -eq 'list') 'Switch must inspect inventory first'
Complete-Job 0 $inventory
Check ($NextJob.Args[-1] -eq '2-4') 'Wrong adapter attached'
Complete-Job
Check ($NextJob.File -eq 'wsl.exe') 'Bluetooth initialization missing'
Complete-Job
Check ($NextBackend.Payload.action -eq 'initialize' -and $NextBackend.Payload.usbId -eq '7392:c611' -and $NextBackend.Payload.controller -eq '') 'Initialization inherited the old controller'
$newState=[pscustomobject]@{controller=[pscustomobject]@{available=$true; usbId='7392:c611'; mac='02:00:00:00:00:32'}; route=[pscustomobject]@{target='old_bus'}; devices=@()}
Complete-Backend $newState
Check ($NextBackend.Payload.action -eq 'release' -and $NextBackend.Payload.controller -eq '02:00:00:00:00:31') 'Old route must release under its original controller'
Complete-Backend ([pscustomobject]@{route=@{}})
Check ($Settings.UsbId -eq '7392:c611' -and $Settings.Controller -eq '02:00:00:00:00:32') 'New hardware was not committed'
Check ($NextJob.Args[0] -eq 'detach' -and $NextJob.Args[-1] -eq '2-14') 'Only the old adapter should detach'
Complete-Job
Check ($NextBackend.Payload.action -eq 'status' -and $NextBackend.Payload.usbId -eq '7392:c611') 'Pairing list queried on wrong adapter'
Complete-Backend $newState
Check ($NextBackend.Payload.action -eq 'scan' -and $tabs.SelectedIndex -eq 1) 'New empty adapter must open pairing and scan'
Check ($Settings.Source -eq 'phone:02:00:00:00:00:10' -and $Settings.Outputs.speaker.Volume -eq 58 -and $Settings.Outputs.speaker.Delay -eq 391) 'Speaker settings were lost'

# A failed attach must not commit the pending adapter selection.
$inventory="2-14  8087:0026  Intel Bluetooth  Shared`n2-4  7392:c611  Edimax Bluetooth  Attached"
$adapters=@(Parse-Adapters $inventory); Render-Adapters $adapters; $adapterCombo.SelectedItem=$adapters[0]
$script:NextBackend=$null
Start-Center; Complete-Job 0 $inventory; Complete-Job 1 'attach failed'
Check ($Settings.UsbId -eq '7392:c611' -and $null -eq $NextBackend) 'Attach failure changed current hardware or released playback'

# No automatic scan when the selected adapter already has paired devices.
$adapterCombo.SelectedItem=@($adapterCombo.Items | Where-Object {$_.BusId -eq '2-4'})[0]
Start-Center; Complete-Job 0 $inventory; Complete-Job
Complete-Backend $newState
Check ($NextBackend.Payload.action -eq 'status') 'Same-adapter apply must not release playback'
Complete-Backend ([pscustomobject]@{devices=@([pscustomobject]@{paired=$true})})
Check ($null -eq $NextBackend) 'Existing pairings must not start an automatic scan'

# Both adapters must remain attached when the former output becomes the input.
$Settings.BusId='2-14'; $Settings.UsbId='8087:0026'; $Settings.Controller='02:00:00:00:00:31'
$inventory="2-14  8087:0026  Intel Bluetooth  Attached`n2-4  7392:c611  Edimax Bluetooth  Attached"
$adapters=@(Parse-Adapters $inventory); Render-Adapters $adapters
$adapterCombo.SelectedItem=@($adapterCombo.Items | Where-Object {$_.BusId -eq '2-4'})[0]
$inputAdapterCombo.SelectedItem=@($inputAdapterCombo.Items | Where-Object {$_.BusId -eq '2-14'})[0]
Start-Center; Complete-Job 0 $inventory
Check ($NextJob.Args[0] -eq 'list') 'Split setup must retain a second inventory check for output'
Complete-Job 0 $inventory; Complete-Job
Check ($NextBackend.Payload.inputUsbId -eq '8087:0026' -and $NextBackend.Payload.usbId -eq '7392:c611') 'Split identities did not reach backend initialization'
$splitState=[pscustomobject]@{controller=$newState.controller; inputController=[pscustomobject]@{available=$true; usbId='8087:0026'; mac='02:00:00:00:00:31'}; route=@{target='old_bus'}; devices=@()}
Complete-Backend $splitState; Complete-Backend ([pscustomobject]@{route=@{}})
Check ($null -eq $NextJob) 'Input adapter was incorrectly detached during output switch'
Check ($Settings.InputController -eq '02:00:00:00:00:31' -and $Settings.InputBusId -eq '2-14') 'Split input settings were not saved'
Check ($NextBackend.Payload.action -eq 'status') 'Split pairing list not refreshed'
'Hardware switch checks passed: targeted attach, delayed commit, old-route release, old-only detach, per-adapter pairing, new-adapter scan, attach rollback, same-adapter reuse.'
$adapterCombo.Dispose(); $inputAdapterCombo.Dispose(); $controllerBox.Dispose(); $adapterStateLabel.Dispose(); $tabs.Dispose()
