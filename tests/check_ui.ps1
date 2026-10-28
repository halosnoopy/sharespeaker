# Synthetic interface checks, dot-sourced only in preview mode.
$savedHardware=@{BusId=$Settings.BusId; UsbId=$Settings.UsbId; Controller=$Settings.Controller}
$adapterFixture=@(Parse-Adapters "2-14  8087:0026  Intel(R) Wireless Bluetooth(R)  Attached`n2-4  7392:c611  Edimax Bluetooth 5.0 USB Adapter  Shared")
if ($adapterFixture.Count -ne 2) { throw 'Both Bluetooth adapters must be listed.' }
Render-Adapters $adapterFixture
$adapterCombo.SelectedItem=$adapterFixture[1]
if ($Settings.BusId -ne $savedHardware.BusId -or $Settings.UsbId -ne $savedHardware.UsbId -or $Settings.Controller -ne $savedHardware.Controller) { throw 'Draft hardware selection changed the active adapter.' }
Render-Adapters $adapterFixture
if ($adapterCombo.SelectedItem.BusId -ne '2-4') { throw 'Hardware refresh lost pending selection.' }
Render-State $Snapshot
if ($adapterCombo.SelectedItem.BusId -ne '2-4') { throw 'Status refresh overwrote pending hardware selection.' }
function script:Start-Backend([hashtable]$Payload,[string]$Label,[int]$Timeout,[scriptblock]$Then) { $script:CapturedPayload=$Payload }
if (-not ($sourceCombo.Items | Where-Object {$_.Id -eq 'phone:02:00:00:00:00:10'})) { throw 'Phone missing from source selector.' }
if ($sourceCombo.Items | Where-Object {$_.Id -eq 'phone:02:00:00:00:00:21'}) { throw 'Bose incorrectly listed as phone input.' }
if ($form.Text -ne 'ShareSpeaker' -or $title.Text -ne 'ShareSpeaker') { throw 'Application branding is incorrect.' }
function script:Show-NameDialog([string]$Title,[string]$CurrentName,[string]$DefaultName,[string]$Note) { $script:NameDialogArguments=@($Title,$CurrentName,$DefaultName,$Note); return $script:NameDialogValue }
$tabs.SelectedIndex=1
$deviceGrid.ClearSelection(); @($deviceGrid.Rows | Where-Object {$_.Tag -eq '02:00:00:00:00:21'})[0].Selected=$true
$script:NameDialogValue=$null; $capturedBefore=$CapturedPayload; $renameDeviceButton.PerformClick()
if ($CapturedPayload -ne $capturedBefore) { throw 'Cancelling a rename submitted a command.' }
$script:NameDialogValue=' '; $renameDeviceButton.PerformClick()
if ($CapturedPayload -ne $capturedBefore) { throw 'Blank device name submitted a command.' }
$script:NameDialogValue='Living room speaker'; $renameDeviceButton.PerformClick()
if ($CapturedPayload.action -ne 'rename-device' -or $CapturedPayload.mac -ne '02:00:00:00:00:21' -or $CapturedPayload.name -ne 'Living room speaker' -or $CapturedPayload.deviceDirection -ne 'output') { throw 'Rename targeted the wrong device or adapter role.' }
$originalSnapshot=($Snapshot | ConvertTo-Json -Depth 15 | ConvertFrom-Json)
$renamed=($Snapshot | ConvertTo-Json -Depth 15 | ConvertFrom-Json); @($renamed.devices | Where-Object {$_.mac -eq '02:00:00:00:00:21'})[0].name='Living room speaker'
Render-State $renamed
if ($OutputCards['02:00:00:00:00:21'].Name.Text -ne 'Living room speaker' -or $OutputCards['02:00:00:00:00:21'].VolumeNumber.Value -ne 60 -or $Settings.Source -ne 'phone:02:00:00:00:00:10') { throw 'Renamed card lost playback settings or failed to refresh.' }
$deviceGrid.ClearSelection(); @($deviceGrid.Rows | Where-Object {$_.Tag -eq '02:00:00:00:00:10'})[0].Selected=$true
$script:NameDialogValue='Music phone'; $renameDeviceButton.PerformClick()
if ($CapturedPayload.deviceDirection -ne 'input' -or $CapturedPayload.mac -ne '02:00:00:00:00:10') { throw 'Phone rename targeted output hardware.' }
@($renamed.devices | Where-Object {$_.mac -eq '02:00:00:00:00:10'})[0].name='Music phone'; Render-State $renamed
if ($sourceCombo.SelectedItem.Label -notlike 'Music phone*') { throw 'Phone nickname did not update the input selector.' }
$tabs.SelectedIndex=2
$capturedBefore=$CapturedPayload; $script:NameDialogValue='Receiver'; Rename-Adapter 'output'
if ($CapturedPayload -ne $capturedBefore) { throw 'Pending hardware selection was renamed before it was applied.' }
$adapterCombo.SelectedIndex=-1; $inputAdapterCombo.SelectedIndex=0
Rename-Adapter 'output'
if ($CapturedPayload.action -ne 'rename-adapter' -or $CapturedPayload.direction -ne 'output' -or $CapturedPayload.name -ne 'Receiver') { throw 'Output adapter rename request is incorrect.' }
Rename-Adapter 'input'
if ($CapturedPayload.direction -ne 'input' -or $NameDialogArguments[2] -ne 'ShareSpeaker') { throw 'Shared input adapter rename request is incorrect.' }
$editor=New-NameDialog 'Rename device' 'Example device' 'Default device' 'Choose a local nickname.'
try {
    $editor.Form.Show($form); $editor.Reset.PerformClick()
    if ($editor.Name.Text -ne 'Default device') { throw 'Default name was not restored in the editor.' }
    $editor.Name.Text=''; $editor.Save.PerformClick()
    if (-not $editor.Form.Visible -or -not $editor.Error.Text) { throw 'Invalid name closed the editor or hid validation.' }
    $editor.Name.Text='New name'; $editor.Save.PerformClick()
    if ($editor.Form.DialogResult -ne 'OK') { throw 'Valid name could not be saved from the editor.' }
} finally { $editor.Form.Dispose() }
Render-State $originalSnapshot; $tabs.SelectedIndex=0
foreach ($wanted in @('02:00:00:00:00:22','02:00:00:00:00:21','both')) {
    foreach ($mac in $OutputCards.Keys) { $OutputCards[$mac].Selected.Checked=($wanted -eq 'both' -or $mac -eq $wanted) }
    $playButton.PerformClick()
    $expected=if ($wanted -eq 'both') {2} else {1}
    if (@($CapturedPayload.outputs).Count -ne $expected -or $CapturedPayload.source -ne 'phone:02:00:00:00:00:10') { throw 'Playback selection did not reach backend payload.' }
    if ($wanted -ne 'both' -and $CapturedPayload.outputs[0].mac -ne $wanted) { throw 'Wrong speaker selected.' }
}
$boseCard=$OutputCards['02:00:00:00:00:21']
$boseCard.DelaySlider.Value=250; $boseCard.VolumeNumber.Value=62; $playButton.PerformClick()
$bosePayload=@($CapturedPayload.outputs | Where-Object {$_.mac -eq '02:00:00:00:00:21'})[0]
if ($bosePayload.delay -ne 250 -or $bosePayload.volume -ne 62) { throw 'Volume/delay edit was not applied.' }
if ($boseCard.DelayNumber.Value -ne 250 -or $boseCard.VolumeSlider.Value -ne 62) { throw 'Sliders and number inputs are not synchronized.' }
if ($pendingLabel.Text -notlike '*Unapplied*') { throw 'Pending edits are not visible.' }
$capturedBefore=$CapturedPayload
$boseCard.Plus.PerformClick()
if ($boseCard.DelayNumber.Value -ne 260 -or $boseCard.DelaySlider.Value -ne 260 -or $CapturedPayload -ne $capturedBefore) { throw 'Delay step changed playback or failed to synchronize.' }
$boseCard.Minus.PerformClick()
$originalPanel=$boseCard.Panel
Render-State $Snapshot
if ($OutputCards['02:00:00:00:00:21'].Panel -ne $originalPanel -or $boseCard.DelayNumber.Value -ne 250 -or $boseCard.VolumeNumber.Value -ne 62) { throw 'Status refresh replaced cards or lost draft edits.' }
$boseCard.DelayNumber.Value=3000; $boseCard.Plus.PerformClick()
if ($boseCard.DelaySlider.Value -ne 3000) { throw 'Delay upper limit failed.' }
$boseCard.DelayNumber.Value=0; $boseCard.Minus.PerformClick()
if ($boseCard.DelaySlider.Value -ne 0) { throw 'Delay lower limit failed.' }
$boseCard.DelayNumber.Value=250
Set-Busy $true 'UI check'
if ($boseCard.Panel.Enabled -or $sourceCombo.Enabled -or $toolsToggle.Enabled -or $testButton.Enabled -or $recoverButton.Enabled) { throw 'Busy state is inconsistent.' }
Set-Busy $false
$toolsToggle.PerformClick()
if (-not $playTools.Visible) { throw 'More menu cannot be opened.' }
$playTools.Close()
$testButton.PerformClick()
if ($CapturedPayload.action -ne 'test' -or $Settings.Source -ne 'phone:02:00:00:00:00:10') { throw 'Test tone changed selected phone input.' }
$recoverButton.PerformClick()
if ($CapturedPayload.action -ne 'recover-input' -or $CapturedPayload.source -ne 'phone:02:00:00:00:00:10') { throw 'Phone recovery lost the selected input.' }
$stopButton.PerformClick()
if ($CapturedPayload.action -ne 'stop') { throw 'Pause button did not submit stop action.' }
$paused=($Snapshot | ConvertTo-Json -Depth 15 | ConvertFrom-Json)
$paused.route | Add-Member -NotePropertyName paused -NotePropertyValue $true -Force
Render-State $paused
if ($playButton.Text -notlike '*Resume*' -or $pendingLabel.Text -notlike '*Paused*') { throw 'Paused state is not visible.' }
$playButton.PerformClick()
if ($CapturedPayload.action -ne 'route' -or $CapturedPayload.source -ne 'phone:02:00:00:00:00:10' -or @($CapturedPayload.outputs).Count -ne 2) { throw 'Resume lost input or output selection.' }
$sourceCombo.SelectedItem=@($sourceCombo.Items | Where-Object {$_.Id -eq 'local-test'})[0]
$playButton.PerformClick()
if ($CapturedPayload.source -ne 'local-test') { throw 'Local test source did not reach backend.' }
$empty=($Snapshot | ConvertTo-Json -Depth 15 | ConvertFrom-Json); $empty.route=[pscustomobject]@{}
Render-State $empty
if ($routeLabel.Text -notlike '*Forwarding has not started*') { throw 'Empty route did not render.' }
$third=($Snapshot | ConvertTo-Json -Depth 15 | ConvertFrom-Json)
$third.devices+=@([pscustomobject]@{mac='AA:BB:CC:DD:EE:03'; name='Third speaker'; role='output'; paired=$true; trusted=$true; connected=$false; status='Disconnected'})
Render-State $third
if ($OutputCards.Count -ne 3 -or $OutputCards['AA:BB:CC:DD:EE:03'].Selected.Checked) { throw 'Third output did not get its own unselected card.' }
if ($OutputPageCount -ne 2 -or @($OutputCards.Values | Where-Object {$_.Panel.Visible}).Count -ne 2) { throw 'Extra speakers were not paged as complete cards.' }
$OutputCards['AA:BB:CC:DD:EE:03'].Selected.Checked=$true
$nextPage.PerformClick()
if ($OutputPage -ne 1 -or -not $OutputCards['AA:BB:CC:DD:EE:03'].Panel.Visible) { throw 'Second page did not expose the third speaker.' }
Apply-Playback
if (@($CapturedPayload.outputs).Count -ne 3) { throw 'Playback lost selected outputs from other pages.' }
$previousPage.PerformClick()
if ($OutputPage -ne 0) { throw 'Previous page failed.' }
$third.devices[-1].role='input'; $Settings.Roles['AA:BB:CC:DD:EE:03']='input'
Render-State $third
if ($OutputCards.ContainsKey('AA:BB:CC:DD:EE:03') -or $MutationControls.Contains($OutputCards['02:00:00:00:00:21'].Panel) -ne $true) { throw 'Changing device role removed the wrong card or left stale controls.' }
$script:OutputInteraction=$true; if (-not (Test-PlaybackEditing)) { throw 'Dragging does not defer status rendering.' }; $script:OutputInteraction=$false
$boseCard.VolumeNumber.Text='73'; [void]$form.ValidateChildren(); Apply-Playback
if (@($CapturedPayload.outputs | Where-Object {$_.mac -eq '02:00:00:00:00:21'})[0].volume -ne 73) { throw 'Typed numeric edits were not committed on apply.' }
$normalSize=$form.Size; $form.Size=$form.MinimumSize; $form.PerformLayout(); Resize-OutputCards
foreach ($card in $OutputCards.Values) { if ($card.Panel.Visible -and ($card.Panel.Right -gt $outputPanel.ClientSize.Width -or $card.Panel.Bottom -gt $outputPanel.ClientSize.Height)) { throw 'Card is clipped at the minimum window size.' } }
$beforeStatus=$outputPanel.Bounds
Show-Status 'Current operation complete.'
Show-Status 'Latest status only.'
if ($statusLabel.Text -ne 'Latest status only.' -or $outputPanel.Bounds -ne $beforeStatus) { throw 'Status messages accumulated or changed card space.' }
$beforeMenu=$outputPanel.Bounds
$toolsToggle.PerformClick(); if (-not $playTools.Visible -or $outputPanel.Bounds -ne $beforeMenu) { throw 'More menu changed card space.' }; $playTools.Close()
if ($startButton.Parent -ne $bottomActions -or $refreshButton.Parent -ne $bottomActions) { throw 'Action placement is incorrect.' }
if ($outputPanel.AutoScroll) { throw 'Card area must page instead of clipping a scrolling row.' }
$form.Size=$normalSize; $form.PerformLayout()
if ($form.Controls.Find('Logs',$true).Count) { throw 'Unexpected log control.' }
foreach ($card in $OutputCards.Values) { $card.Selected.Checked=$false }; $capturedBefore=$CapturedPayload; Apply-Playback
if ($statusLabel.Text -ne 'Select at least one speaker.' -or $CapturedPayload -ne $capturedBefore) { throw 'Empty output selection submitted playback or hid the validation message.' }
@{ok=$true; checks=@('ShareSpeaker branding','device rename payload and cancellation','rename validation','renamed speaker preserves playback settings','phone rename and selector','adapter rename targets and pending-selection guard','name editor validation and default reset','phone source','speaker classification','ACTON only','Bose only','dual output','volume and delay payload','slider-number synchronization','delay fine adjustment and limits','draft edits do not submit playback','refresh preserves draft and card identity','busy state','pending edits visible','more menu opens','independent test button','phone recovery payload','pause state and resume payload','local test source','empty route','third output card','complete-card pagination','page navigation','playback includes selections on other pages','role change removes stale card','drag defers refresh','typed numeric commit','minimum window shows entire cards','status replaces previous message without changing card bounds','more menu leaves card bounds unchanged','bottom actions','no partial scrolling card row','validation shows current message without submitting playback')} | ConvertTo-Json | Set-Content (Join-Path $Root 'dist/gui-routing-check.json') -Encoding UTF8
$script:UiCheckSuccess=$true
$script:ExitRequested=$true; $form.Close(); return
