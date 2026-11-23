import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

const uuid='macbook-thermals@ubuntu-thermals.local', role='ubuntu-macbook-thermals';
const dir=GLib.getenv('THERMAL_TEST_DIR'), src=GLib.getenv('THERMAL_TEST_SOURCE');
function assert(condition,message) { if (!condition) throw new Error(message); }
async function until(predicate,message) {
    for(let i=0;i<95;i++) {
        if(predicate()) return;
        await Scripting.sleep(100);
    }
    throw new Error(message);
}
function launch() {
    const launcher=new Gio.SubprocessLauncher({flags:Gio.SubprocessFlags.NONE});
    launcher.setenv('WAYLAND_DISPLAY','gnome-shell-test-display',true);
    launcher.setenv('GDK_BACKEND','wayland',true);
    return launcher.spawnv(['/usr/bin/python3','-E',src+'/test/test-app.py']);
}
function box(actor) {
    return [...actor.get_transformed_position(),...actor.get_transformed_size()].map(Math.round);
}
function same(a,b){return a.every((x,i)=>Math.abs(x-b[i])<=1);}
function current() {return Main.extensionManager.lookup(uuid)?.stateObj;}
export async function run() {
    assert(Main.layoutManager.primaryMonitor && GLib.getenv('XDG_RUNTIME_DIR')===dir+'/runtime','unisolated test runtime');
    await until(()=>current()?.watch,'Cold login did not start the panel watcher');
    assert(!Main.panel.statusArea[role], 'Monitor active at cold login');
    console.log('THERMAL-TEST cold login: zero panel buttons, waiting for the service');
    await until(()=>!Main.layoutManager._startingUp,'Shell startup never finished');
    Main.overview.hide();
    await until(()=>!Main.overview.visible,'Overview did not close');
    console.log('THERMAL-INPUT desktop '+[Main.actionMode,Main.modalCount,String(global.stage.get_key_focus())]);
    const app=launch();
    await until(()=>Main.panel.statusArea[role] && current()?.state?.history?.length,'Manual launch did not attach dashboard');
    const p=current();
    // A physical click well outside the anchored popup exercises GNOME's menu grab.
    const seat=global.stage.get_context().get_backend().get_default_seat();
    const mouse=seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    async function click(x,y) {
        mouse.notify_absolute_motion(GLib.get_monotonic_time(),x,y);
        await Scripting.sleep(70);
        seat.warp_pointer(Math.round(x),Math.round(y));
        await Scripting.sleep(70);
        mouse.notify_button(GLib.get_monotonic_time(),1,Clutter.ButtonState.PRESSED);
        await Scripting.sleep(70);
        mouse.notify_button(GLib.get_monotonic_time(),1,Clutter.ButtonState.RELEASED);
    }
    function clickActor(actor) {
        const [x,y,w,height]=box(actor);
        return click(x+w/2,y+height/2);
    }
    await until(()=>p.button.mapped && p.button.width>0,'Button never allocated');
    await Scripting.sleep(300);
    await clickActor(p.button);
    await until(()=>p.button.menu.isOpen,'Left click did not open panel');
    await Scripting.sleep(300);
    const start=box(p.button.menu.actor);
    if (GLib.getenv('THERMAL_TEST_PANEL')==='bottom') {
        assert(box(p.button)[1]>Main.layoutManager.primaryMonitor.height/2,'Expected Dash to Panel at bottom');
        assert(start[1]+start[3]<=box(p.button)[1]+2,'Popup did not anchor above bottom panel');
        console.log('THERMAL-TEST installed Dash to Panel verified on bottom edge');
    }
    assert(start[2]>200 && start[3]>150,'Invalid popup geometry: '+start);
    function visibleLabels(actor) {
        if (!actor.visible) return [];
        return [actor.text, actor.label, ...actor.get_children().flatMap(visibleLabels)].filter(s=>typeof s==='string');
    }
    const labels=visibleLabels(p.button.menu.actor);
    for (const removed of ['MacBook thermals','Stop monitor','Last 2 minutes','Power profile','Battery'])
        assert(!labels.some(s=>s.toLowerCase().includes(removed.toLowerCase())), 'Unwanted popup label: '+removed);
    assert(p.powerMenu.button.label.replace(/[▾▴]/g,'').trim()===p.state.name,'Incorrect selector title');
    assert(/^[0-9]+\.[0-9] W$/.test(p.draw.text) && !p.footerLabel.visible,'Missing standalone system wattage');
    const powerBox=box(p.wgraph), cpuBox=box(p.tgraph), speedBox=box(p.fgraph);
    assert(cpuBox[1]+cpuBox[3] <= powerBox[1] && powerBox[1]+powerBox[3] <= speedBox[1], 'Incorrect card order');
    assert(powerBox[0]===cpuBox[0] && powerBox[2]===cpuBox[2] && powerBox[3]===cpuBox[3], 'Power graph has inconsistent dimensions');
    const tempBox=box(p.tgraph), fanBox=box(p.fgraph), profileBox=box(p.powerMenu.button);
    function equalSelectors() {
        const a=box(p.powerMenu.button), b=box(p.fanMenu.button);
        assert(Math.abs(a[2]-b[2])<=1 && b[0]-a[0]-a[2]===6,'Unequal selectors: '+[a,b]);
        return [a[2],b[2]];
    }
    equalSelectors();
    if (GLib.getenv('THERMAL_SCREENSHOT')) {
        const {default: Shell} = await import('gi://Shell');
        const output = Gio.File.new_for_path(dir+'/popup.png').replace(null,false,Gio.FileCreateFlags.NONE,null);
        const [x,y,w,h] = box(p.button.menu.actor);
        await new Shell.Screenshot().screenshot_area(x,y,w,h,output);
        output.close(null);
    }
    assert(tempBox[0]===fanBox[0] && tempBox[2]===fanBox[2] && tempBox[2]>=profileBox[2]-24,
        'Graphs are not full width: '+[tempBox,fanBox,profileBox]);
    const speedPercent=()=>Math.round(Math.min(100,100*p.history.at(-1)[3]/p.state.fan_max))+'%';
    assert(p.state.fan_max>0 && p.fan.text===speedPercent(),'Expected percentage of measured max fan RPM by default');
    await clickActor(p.fgraph);
    await until(()=>p.fan.text.endsWith(' rpm'),'Fan graph click did not switch to RPM');
    assert(p.button.menu.isOpen,'Clicking fan graph closed the panel');
    await clickActor(p.fgraph);
    await until(()=>p.fan.text===speedPercent(),'Fan graph click did not restore percent');
    await clickActor(p.powerMenu.button);
    await Scripting.sleep(120);
    const expanded=box(p.button.menu.actor);
    assert(p.powerMenu.list.visible && same(start,expanded),'Dropdown moved/resized dashboard: '+[start,expanded]);
    const chart=box(p.tgraph), dropdown=box(p.powerMenu.list);
    assert(dropdown[1]+dropdown[3]>chart[1] && dropdown[1]<chart[1]+chart[3],'Dropdown does not overlay temperature graph');
    const gap=dropdown[1]-(profileBox[1]+profileBox[3]);
    assert(gap>=0 && gap<=2,'Selector list gap: '+gap);
    const firstChoice=box([...p.powerMenu.options.values()][0]);
    assert(firstChoice[1]-(profileBox[1]+profileBox[3])<=6,'Excess space above first option');
    console.log('THERMAL-TEST graph widths '+tempBox[2]+' / '+fanBox[2]+' px, dropdown gap '+gap+' px, fan toggled in place');
    await clickActor(p.fanMenu.button);
    await until(()=>p.fanMenu.list.visible && !p.powerMenu.list.visible,'Dropdowns overlapped');
    await Scripting.sleep(100);
    assert(same(start,box(p.button.menu.actor)),'Fan dropdown resized popup');
    const oldPower=p.state.key;
    await clickActor(p.fanMenu.options.get('fan-early'));
    await until(()=>p.state.fan_key==='fan-early' && !p.state.busy,'Fan profile did not change');
    assert(p.state.key===oldPower && p.fanMenu.button.label.includes('Aggressive'),'Fan selector changed power');
    await clickActor(p.fanMenu.button);
    await Scripting.sleep(100);
    await clickActor(p.fanMenu.options.get('fan-smooth'));
    await until(()=>p.state.fan_key==='fan-smooth' && !p.state.busy,'Smooth fan was not selectable');
    await Scripting.sleep(100);
    await clickActor(p.fanMenu.button);
    await Scripting.sleep(100);
    await clickActor(p.fanMenu.options.get('fan-smooth-plus'));
    await until(()=>p.state.fan_key==='fan-smooth-plus' && !p.state.busy,'Smooth+ fan was not selectable');
    assert(p.state.key===oldPower && p.fanMenu.button.label.includes('Smooth+'),'Smooth+ changed the CPU selection');
    console.log('THERMAL-TEST equal selector widths '+equalSelectors()+' px; Smooth+ passed');
    await clickActor(p.fanMenu.button);
    await Scripting.sleep(100);
    await clickActor(p.fanMenu.options.get('fan-auto'));
    await until(()=>p.state.fan_key==='fan-auto' && !p.state.busy,'Could not restore automatic fan');
    await clickActor(p.powerMenu.button);
    await Scripting.sleep(100);
    assert(p.powerMenu.options.get('restore').label.includes('Stock'),'Missing Stock power option');
    await clickActor(p.powerMenu.options.get('restore'));
    await until(()=>p.state.key==='restore' && !p.state.busy,'Stock power selection failed');
    assert(p.fanMenu.button.label.includes('Stock') && p.powerMenu.button.label.includes('Stock'),'Stock selections did not round trip');
    equalSelectors();
    await clickActor(p.powerMenu.button);
    console.log('THERMAL-TEST independent power and fan dropdowns, overlay geometry and both fan selections passed');
    const outsideX=start[0]>200?20:Main.layoutManager.primaryMonitor.width-20;
    const outsideY=Main.layoutManager.primaryMonitor.height/2;
    await click(outsideX,outsideY);
    await until(()=>!p.button.menu.isOpen,'Outside click failed to dismiss');
    assert(!p.powerMenu.list.visible,'Profile list remained expanded');
    const tray=box(p.button);
    await click(tray[0]+tray[2]/2,tray[1]+tray[3]/2);
    await until(()=>p.button.menu.isOpen,'Physical left click did not reopen tray');
    await Scripting.sleep(180);
    assert(same(start,box(p.button.menu.actor)),'Reopen left a gap: '+[start,box(p.button.menu.actor)]);
    assert(p.cpu.text.includes('°C') && p.fan.text.includes('%'),'Live hardware sensors unavailable: '+p.cpu.text+'/'+p.fan.text);
    await until(()=>p.history.length>=2,'No new readings');
    console.log('THERMAL-TEST left click, outside dismiss, reopen and overlay OK; geometry '+start+'; '+p.cpu.text+' '+p.fan.text);
    await clickActor(p.powerMenu.button);
    const pick='mild';
    await until(()=>p.powerMenu.list.visible,'Selector failed to open');
    await Scripting.sleep(100);
    await clickActor(p.powerMenu.options.get(pick));
    await until(()=>p.state.key===pick && !p.state.busy,'Profile selection did not update');
    assert(!p.powerMenu.list.visible,'Selector did not collapse after choice');
    console.log('THERMAL-TEST profile request and verified UI state (mocked hardware writes)');
    Main.sessionMode.pushMode('unlock-dialog');
    await until(()=>!Main.panel.statusArea[role],'Panel did not hide on lock');
    await Scripting.sleep(300);
    Main.sessionMode.popMode('unlock-dialog');
    await until(()=>Main.panel.statusArea[role] && current().history.length>=2,'Monitor failed to return after unlock');
    console.log('THERMAL-TEST screen lock/unlock shell modes retain monitor');
    const present=launch();
    await until(()=>current().button.menu.isOpen,'Relaunch failed to present running panel');
    // GNOME temporarily invokes disable/enable on later extensions in place.
    const before = current().owner;
    current().disable();
    await Scripting.sleep(250);
    current().enable();
    await until(()=>current().button && current().owner===before,'Rebase lost the service');
    await current().call('Stop');  // Legacy Shell code still cached in some sessions
    current().disable();
    current().enable();
    await until(()=>current().button && current().owner===before,'Legacy Stop terminated the service');
    app.force_exit();
    await until(()=>!Main.panel.statusArea[role] && current()?.watch,'Backend loss left a stale button or disabled its watcher');
    const restarted=launch();
    await until(()=>current().button && current().owner!==before,'Panel did not attach to replacement owner');
    restarted.force_exit();
    await until(()=>!Main.panel.statusArea[role], 'Replacement backend left a stale panel');
    console.log('THERMAL-TEST rebase and legacy Stop kept owner; process replacement reattached automatically');
    // Reproduce the first real login: the custom extension isn't in the
    // Shell catalog yet, while Ubuntu AppIndicators is running.
    await Main.extensionManager.unloadExtension(Main.extensionManager.lookup(uuid));
    assert(Main.extensionManager.enableExtension('ubuntu-appindicators@ubuntu.com'),'Unable to start tray host');
    const standard=launch();
    const trayIcon=()=>Object.values(Main.panel.statusArea).find(b=>b._indicator?.id==='macbook-thermals');
    await until(()=>trayIcon()?.visible && trayIcon().menu.numMenuItems>3,'Standard tray icon or menu missing');
    await Scripting.sleep(300);
    const icon=trayIcon();
    assert(!icon.menu.isOpen,'Popup opened without clicking the thermometer');
    const windows=()=>global.get_window_actors().filter(a=>a.meta_window?.get_pid()===Number(standard.get_identifier()));
    assert(windows().length===0,'Monitor created a desktop window');
    await clickActor(icon);
    await until(()=>icon.menu.isOpen,'Single left click failed on the standard tray');
    await Scripting.sleep(250);
    const menu=icon.menu, items=()=>menu._getMenuItems();
    const selectors=()=>items().filter(i=>i.menu);
    const selector=()=>selectors()[0];
    const fanSelector=()=>selectors()[1];
    const fanRow=()=>items().find(i=>i.label?.text.startsWith('Fan speed'));
    assert(selector() && fanRow()?.label.text.includes('%'),'Missing default fan percent');
    assert(!items().some(i=>/Battery|Stop monitor|Last 2 minutes|Power profile/i.test(i.label?.text ?? '')),'Unwanted fallback labels');
    assert(items().some(i=>/^System draw +[0-9]+\.[0-9] W$/.test(i.label?.text ?? '')),'Missing fallback system wattage');
    const plots=items().filter(i=>i.label?.text.includes('\n'));
    assert(plots.length===3 && plots.every(plot=>box(plot.label)[2]===box(plots[0].label)[2]), 'Fallback plots have unequal width');
    await clickActor(plots[2]);
    await until(()=>!menu.isOpen,'Expected standard tray to dismiss on activation');
    await clickActor(icon);
    await until(()=>fanRow()?.label.text.endsWith('rpm'),'Fallback plot did not toggle to RPM');
    await until(()=>menu.isOpen && selector()?.mapped,'Fallback menu did not finish reopening');
    await Scripting.sleep(300);
    const normal=box(menu.actor);
    await clickActor(selector());
    await Scripting.sleep(250);
    console.log('THERMAL-TEST fallback submenu '+[menu.isOpen,selector()?.menu.isOpen,items()[2]?.visible,box(menu.actor)]);
    await until(()=>selector()?.menu.isOpen && !items()[2]?.visible,'Profile choices did not replace the temperature rows');
    await Scripting.sleep(250);
    assert(box(menu.actor)[3]<=normal[3]+1,'Profile list enlarged the system tray popup');
    console.log('THERMAL-TEST standard tray rectangles '+normal+' expanded '+box(menu.actor));
    await click(outsideX,outsideY);
    await until(()=>!menu.isOpen,'Outside click failed on standard tray');
    await clickActor(icon);
    await until(()=>menu.isOpen && items()[2]?.visible && !selector().menu.isOpen,'Reopen failed to restore temperature graph');
    await Scripting.sleep(250);
    assert(same(normal,box(menu.actor)),'Tray shifted after click-away and reopen: '+[normal,box(menu.actor)]);
    await clickActor(selector());
    await until(()=>selector().menu.isOpen,'Profile dropdown did not reopen');
    await Scripting.sleep(200);
    const option=selector().menu._getMenuItems().find(i=>i.label?.text.startsWith('Fast'));
    assert(option,'Missing Fast profile');
    await clickActor(option);
    await until(()=>!menu.isOpen,'Selecting a profile did not dismiss the tray menu');
    await clickActor(icon);
    await until(()=>menu.isOpen,'Could not reopen tray after a selection');
    await until(()=>selector()?.label.text.includes('Fast'),'Reopened menu did not show selected profile');
    await Scripting.sleep(300);
    assert(windows().length===0,'A tray action opened a desktop window');
    assert(fanSelector()?.label.text==='Stock','Missing independent tray fan selector');
    await clickActor(fanSelector());
    await until(()=>fanSelector()?.menu.isOpen,'Fan submenu did not open');
    await Scripting.sleep(250);
    await clickActor(fanSelector().menu._getMenuItems().find(i=>i.label.text.startsWith('Smooth')));
    await until(()=>!menu.isOpen,'Fan choice did not dismiss standard tray');
    await clickActor(icon);
    await until(()=>menu.isOpen && fanSelector()?.label.text==='Smooth','Tray fan choice did not update');
    assert(selector().label.text==='Fast','Tray fan change altered the CPU profile');
    const quit=Gio.Subprocess.new(['/usr/bin/gapplication','action','io.local.MacBookThermals','quit'],Gio.SubprocessFlags.NONE);
    await quit.wait_check_async(null);
    await until(()=>!trayIcon(),'Stop did not remove the tray icon');
    console.log('THERMAL-TEST Ubuntu tray: red thermometer, single click, live plots, profile selection, click-away/reopen, quit, zero windows');
    console.log('THERMAL-TEST PASS');
}
