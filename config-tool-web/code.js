import crc32 from './crc.js';
import usages from './usages.js';
import examples from './examples.js';

const REPORT_ID_CONFIG = 100;
const UNMAPPED_PASSTHROUGH_FLAG = 0x01;
const STICKY_FLAG = 0x01;
const CONFIG_SIZE = 32;
const CONFIG_VERSION = 11;
const VENDOR_ID = 0xCAFE;
const PRODUCT_ID = 0xBAF3;
const DEFAULT_PARTIAL_SCROLL_TIMEOUT = 1000000;
const DEFAULT_SCALING = 1000;
const DEFAULT_SENSITIVITY = 1000;
const NSCREENS = 6;
const NPROFILES = 4;
const MAX_MAPPINGS_PER_PROFILE = 32;

const SET_CONFIG = 2;
const GET_CONFIG = 3;
const CLEAR_MAPPING = 4;
const ADD_MAPPING = 5;
const GET_MAPPING = 6;
const PERSIST_CONFIG = 7;
const GET_OUR_USAGES = 8
const GET_THEIR_USAGES = 9
const SUSPEND = 10;
const RESUME = 11;
const SET_SCREEN = 12;
const GET_SCREEN = 13;
const SELECT_PROFILE = 14;
const SET_ACTIVE_PROFILE = 15;
const SET_PROFILE_COUNT = 16;
const GET_DEVICE_INFO = 17;

const UINT8 = Symbol('uint8');
const UINT16 = Symbol('uint16');
const UINT32 = Symbol('uint32');
const INT32 = Symbol('int32');

let device = null;
let modal = null;
let extra_usages = [];

function make_default_profile() {
    return {
        'unmapped_passthrough': true,
        'partial_scroll_timeout': DEFAULT_PARTIAL_SCROLL_TIMEOUT,
        'interval_override': 1,
        'constraint_mode': 2,
        'offscreen_sensitivity': 4000,
        'coord_scale': 0,
        'edge_resistance': 0,
        'jiggle_interval': 0,
        'screens': [
            { 'x': 0, 'y': 0, 'w': 16000000, 'h': 9000000, 'sensitivity': 4000, 'output': 0, 'drag_gain': 1000, 'drag_curve_k': 0 },
            { 'x': 16000000, 'y': 0, 'w': 16000000, 'h': 9000000, 'sensitivity': 4000, 'output': 1, 'drag_gain': 1000, 'drag_curve_k': 0 }
        ],
        'mappings': [{
            'source_usage': '0x00000000',
            'target_usage': '0x00000000',
            'layer': 0,
            'sticky': false,
            'scaling': DEFAULT_SCALING,
        }]
    };
}

// device_state mirrors what's persisted on the Pico: up to NPROFILES profiles,
// one of which is the "active" one whose contents are mirrored into the live
// remapper globals. current_profile_idx tracks which profile the UI is editing
// right now (independent of which one is active on the device).
let device_state = {
    version: CONFIG_VERSION,
    active_profile: 0,
    profiles: [make_default_profile()],
};
let current_profile_idx = 0;
// `config` is a live reference to the currently-edited profile. All the
// existing screen/mapping/scalar handlers read and write through it; we just
// re-point it whenever the user selects a different profile.
let config = device_state.profiles[0];

const ignored_usages = new Set([
]);

let layout = {
    displays: [],
    remote: { w: 2056, h: 1329, edge: 'left', edge_resistance_px: 0 }
};

document.addEventListener("DOMContentLoaded", function () {
    document.getElementById("open_device").addEventListener("click", open_device);
    document.getElementById("load_from_device").addEventListener("click", load_from_device);
    document.getElementById("save_to_device").addEventListener("click", save_to_device);
    document.getElementById("add_mapping").addEventListener("click", add_mapping_onclick);
    document.getElementById("download_json").addEventListener("click", download_json);
    document.getElementById("upload_json").addEventListener("click", upload_json);
    document.getElementById("file_input").addEventListener("change", file_uploaded);

    document.getElementById("load_from_device").disabled = true;
    document.getElementById("save_to_device").disabled = true;

    document.getElementById("partial_scroll_timeout_input").addEventListener("change", partial_scroll_timeout_onchange);
    document.getElementById("unmapped_passthrough_checkbox").addEventListener("change", unmapped_passthrough_onchange);
    document.getElementById("interval_override_dropdown").addEventListener("change", interval_override_onchange);
    document.getElementById("constraint_mode_dropdown").addEventListener("change", constraint_mode_onchange);
    document.getElementById("offscreen_sensitivity_input").addEventListener("change", offscreen_sensitivity_onchange);
    document.getElementById("jiggle_interval_input").addEventListener("change", jiggle_interval_onchange);

    document.getElementById("add_screen").addEventListener("click", add_screen_onclick);

    document.getElementById("detect_displays").addEventListener("click", detect_displays_onclick);
    document.getElementById("add_display").addEventListener("click", add_display_onclick);
    document.getElementById("apply_layout").addEventListener("click", apply_layout_onclick);
    for (const id of ["remote_w", "remote_h", "remote_edge", "edge_resistance_input"]) {
        document.getElementById(id).addEventListener("change", remote_onchange);
    }
    window.addEventListener("resize", render_layout_preview);

    document.getElementById("add_profile").addEventListener("click", add_profile_onclick);
    document.getElementById("delete_profile").addEventListener("click", delete_profile_onclick);
    document.getElementById("active_profile_dropdown").addEventListener("change", active_profile_onchange);

    navigator.hid.addEventListener('disconnect', hid_on_disconnect);

    setup_examples();
    modal = new bootstrap.Modal(document.getElementById('usage_modal'), {});
    setup_usages_modal();
    set_ui_state();
});

function select_profile(idx) {
    if (idx < 0 || idx >= device_state.profiles.length) return;
    current_profile_idx = idx;
    config = device_state.profiles[idx];
    set_ui_state();
}

function add_profile_onclick() {
    if (device_state.profiles.length >= NPROFILES) {
        display_error('Maximum ' + NPROFILES + ' profiles supported.');
        return;
    }
    device_state.profiles.push(make_default_profile());
    select_profile(device_state.profiles.length - 1);
}

function delete_profile_onclick() {
    if (device_state.profiles.length <= 1) {
        display_error('At least one profile is required.');
        return;
    }
    // Keep active_profile pointing at the same logical profile after the
    // splice: shift it down if we removed something before it, fall back to
    // slot 0 if we removed it itself.
    if (current_profile_idx === device_state.active_profile) {
        device_state.active_profile = 0;
    } else if (current_profile_idx < device_state.active_profile) {
        device_state.active_profile -= 1;
    }
    device_state.profiles.splice(current_profile_idx, 1);
    select_profile(Math.min(current_profile_idx, device_state.profiles.length - 1));
}

function active_profile_onchange() {
    const v = parseInt(document.getElementById('active_profile_dropdown').value, 10);
    if (!Number.isNaN(v) && v >= 0 && v < device_state.profiles.length) {
        device_state.active_profile = v;
    }
    render_profile_tabs();
}

function render_profile_tabs() {
    const container = document.getElementById('profile_tabs');
    clear_children(container);
    for (let i = 0; i < device_state.profiles.length; i++) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-sm ' + (i === current_profile_idx ? 'btn-primary' : 'btn-outline-primary');
        const isActive = (i === device_state.active_profile);
        btn.textContent = 'Profile ' + (i + 1) + (isActive ? ' (active)' : '');
        btn.addEventListener('click', () => select_profile(i));
        container.appendChild(btn);
    }
    document.getElementById('add_profile').disabled = device_state.profiles.length >= NPROFILES;
    document.getElementById('delete_profile').disabled = device_state.profiles.length <= 1;

    const dropdown = document.getElementById('active_profile_dropdown');
    clear_children(dropdown);
    for (let i = 0; i < device_state.profiles.length; i++) {
        const opt = document.createElement('option');
        opt.value = i;
        opt.textContent = 'Profile ' + (i + 1);
        if (i === device_state.active_profile) opt.selected = true;
        dropdown.appendChild(opt);
    }
}

async function open_device() {
    clear_error();
    let success = false;
    const devices = await navigator.hid.requestDevice({
        filters: [{ vendorId: VENDOR_ID, productId: PRODUCT_ID }]
    }).catch((err) => { display_error(err); });
    if (devices !== undefined && devices.length > 0) {
        device = devices[0];
        if (!device.opened) {
            await device.open().catch((err) => { display_error(err + "\nIf you're on Linux, you might need to give yourself permissions to the appropriate /dev/hidraw* device."); });
        }
        success = device.opened;
        if (success) {
            await get_usages_from_device();
            setup_usages_modal();
        }
    }
    document.getElementById("load_from_device").disabled = !success;
    document.getElementById("save_to_device").disabled = !success;
    if (!success) {
        device = null;
    }
}

async function fetch_device_info() {
    await send_feature_command(GET_DEVICE_INFO);
    const [version, nprofiles, max_mappings, active_profile, profile_count, io_target_slot] =
        await read_config_feature([UINT8, UINT8, UINT8, UINT8, UINT8, UINT8]);
    // A pre-v8 firmware would have ignored GET_DEVICE_INFO and returned an
    // all-zero response with a valid CRC - check_version catches that.
    check_version(version);
    return { nprofiles, max_mappings, active_profile, profile_count, io_target_slot };
}

async function read_one_profile() {
    await send_feature_command(GET_CONFIG);
    const [config_version, flags, partial_scroll_timeout, mapping_count, our_usage_count, their_usage_count,
        interval_override, constraint_mode, offscreen_sensitivity, coord_scale, edge_resistance, jiggle_interval] =
        await read_config_feature([UINT8, UINT8, UINT32, UINT16, UINT16, UINT16, UINT8, UINT8, UINT32, UINT32, UINT32, UINT16]);
    check_version(config_version);

    const profile = {
        'unmapped_passthrough': (flags & UNMAPPED_PASSTHROUGH_FLAG) != 0,
        'partial_scroll_timeout': partial_scroll_timeout,
        'interval_override': interval_override,
        'constraint_mode': constraint_mode,
        'offscreen_sensitivity': offscreen_sensitivity,
        'coord_scale': coord_scale,
        'edge_resistance': edge_resistance,
        'jiggle_interval': jiggle_interval,
        'screens': [],
        'mappings': [],
    };

    for (let i = 0; i < NSCREENS; i++) {
        await send_feature_command(GET_SCREEN, [[UINT32, i]]);
        const [x, y, w, h, sensitivity, output, scale, drag_gain, drag_curve_k] =
            await read_config_feature([UINT32, UINT32, UINT32, UINT32, UINT16, UINT8, UINT16, UINT16, UINT16]);
        if (w > 0 || h > 0) {
            profile['screens'].push({ x, y, w, h, sensitivity, output, scale, drag_gain, drag_curve_k });
        }
    }

    for (let i = 0; i < mapping_count; i++) {
        await send_feature_command(GET_MAPPING, [[UINT32, i]]);
        const [target_usage, source_usage, scaling, layer, mapping_flags] =
            await read_config_feature([UINT32, UINT32, INT32, UINT8, UINT8]);
        profile['mappings'].push({
            'target_usage': '0x' + target_usage.toString(16).padStart(8, '0'),
            'source_usage': '0x' + source_usage.toString(16).padStart(8, '0'),
            'scaling': scaling,
            'layer': layer,
            'sticky': (mapping_flags & STICKY_FLAG) != 0,
        });
    }

    return profile;
}

async function load_from_device() {
    if (device == null) {
        return;
    }
    clear_error();

    try {
        const info = await fetch_device_info();
        const profiles = [];
        for (let slot = 0; slot < info.profile_count; slot++) {
            await send_feature_command(SELECT_PROFILE, [[UINT8, slot]]);
            profiles.push(await read_one_profile());
        }
        // Restore io_target_slot to the active profile so subsequent edits to a
        // single field in the UI (which still go through SELECT_PROFILE on save)
        // don't leave the device pointing at a non-active slot.
        await send_feature_command(SELECT_PROFILE, [[UINT8, info.active_profile]]);

        device_state = {
            version: CONFIG_VERSION,
            active_profile: info.active_profile,
            profiles,
        };
        current_profile_idx = info.active_profile;
        config = device_state.profiles[current_profile_idx];
        set_ui_state();
    } catch (e) {
        display_error(e);
    }
}

async function push_one_profile(slot, profile) {
    if ((profile['mappings'] || []).length > MAX_MAPPINGS_PER_PROFILE) {
        throw new Error('Profile ' + (slot + 1) + ' has ' + profile['mappings'].length +
            ' mappings, exceeds limit of ' + MAX_MAPPINGS_PER_PROFILE + '.');
    }

    await send_feature_command(SELECT_PROFILE, [[UINT8, slot]]);

    await send_feature_command(SET_CONFIG, [
        [UINT8, profile['unmapped_passthrough'] ? UNMAPPED_PASSTHROUGH_FLAG : 0],
        [UINT32, profile['partial_scroll_timeout']],
        [UINT8, profile['interval_override']],
        [UINT8, profile['constraint_mode']],
        [UINT32, profile['offscreen_sensitivity']],
        [UINT32, profile['coord_scale'] || 0],
        [UINT32, profile['edge_resistance'] || 0],
        [UINT16, profile['jiggle_interval'] || 0],
    ]);

    for (let i = 0; i < NSCREENS; i++) {
        const screen = i < profile['screens'].length
            ? profile['screens'][i]
            : { x: 0, y: 0, w: 0, h: 0, sensitivity: 0, output: 0, scale: 0, drag_gain: 0, drag_curve_k: 0 };
        await send_feature_command(SET_SCREEN, [
            [UINT8, i],
            [UINT32, screen['x']],
            [UINT32, screen['y']],
            [UINT32, screen['w']],
            [UINT32, screen['h']],
            [UINT16, screen['sensitivity']],
            [UINT8, screen['output'] || 0],
            [UINT16, screen['scale'] || 0],
            [UINT16, screen['drag_gain'] || 0],
            [UINT16, screen['drag_curve_k'] || 0],
        ]);
    }

    await send_feature_command(CLEAR_MAPPING);
    for (const mapping of profile['mappings']) {
        await send_feature_command(ADD_MAPPING, [
            [UINT32, parseInt(mapping['target_usage'], 16)],
            [UINT32, parseInt(mapping['source_usage'], 16)],
            [INT32, mapping['scaling']],
            [UINT8, mapping['layer']],
            [UINT8, mapping['sticky'] ? STICKY_FLAG : 0],
        ]);
    }
}

async function save_to_device() {
    if (device == null) {
        return;
    }
    clear_error();

    try {
        await send_feature_command(SUSPEND);

        // Push non-active slots first, then the active one last so the live
        // mirror ends up matching the user-selected active profile.
        const active = device_state.active_profile;
        const order = [];
        for (let i = 0; i < device_state.profiles.length; i++) {
            if (i !== active) order.push(i);
        }
        order.push(active);
        for (const slot of order) {
            await push_one_profile(slot, device_state.profiles[slot]);
        }

        await send_feature_command(SET_PROFILE_COUNT, [[UINT8, device_state.profiles.length]]);
        await send_feature_command(SET_ACTIVE_PROFILE, [[UINT8, active]]);
        await send_feature_command(PERSIST_CONFIG);
        await send_feature_command(RESUME);
    } catch (e) {
        display_error(e);
    }
}

async function get_usages_from_device() {
    try {
        await send_feature_command(GET_CONFIG);
        const [config_version, flags, partial_scroll_timeout, mapping_count, our_usage_count, their_usage_count] =
            await read_config_feature([UINT8, UINT8, UINT32, UINT16, UINT16, UINT16]);
        check_version(config_version);

        let extra_usage_set = new Set();

        for (const [command, rle_count] of [
            [GET_OUR_USAGES, our_usage_count],
            [GET_THEIR_USAGES, their_usage_count]
        ]) {
            let i = 0;
            while (i < rle_count) {
                await send_feature_command(command, [[UINT32, i]]);
                const fields = await read_config_feature([UINT32, UINT32, UINT32, UINT32, UINT32, UINT32]);

                for (let j = 0; j < 3; j++) {
                    const usage = fields[2 * j];
                    const count = fields[2 * j + 1];
                    if (usage != 0) {
                        for (let k = 0; k < count; k++) {
                            const u = '0x' + (usage + k).toString(16).padStart(8, '0');
                            if (!(u in usages) && !ignored_usages.has(u)) {
                                extra_usage_set.add(u);
                            }
                        }
                    }
                }

                i += 3;
            }
        }

        extra_usages = Array.from(extra_usage_set);
        extra_usages.sort();
    } catch (e) {
        display_error(e);
    }
}

function set_config_ui_state() {
    document.getElementById('partial_scroll_timeout_input').value = Math.round(config['partial_scroll_timeout'] / 1000);
    document.getElementById('unmapped_passthrough_checkbox').checked = config['unmapped_passthrough'];
    document.getElementById('interval_override_dropdown').value = config['interval_override'];
    document.getElementById('constraint_mode_dropdown').value = config['constraint_mode'];
    document.getElementById('offscreen_sensitivity_input').value = config['offscreen_sensitivity'] / 1000;
    document.getElementById('jiggle_interval_input').value = config['jiggle_interval'] || 0;

    derive_layout_from_config();
    render_layout_ui();
    set_screens_ui_state();
}

function set_mappings_ui_state() {
    clear_children(document.getElementById('mappings'));
    for (const mapping of config['mappings']) {
        add_mapping(mapping);
    }
}

function set_ui_state() {
    render_profile_tabs();
    set_config_ui_state();
    set_mappings_ui_state();
}

function add_mapping(mapping) {
    const template = document.getElementById("mapping_template");
    const container = document.getElementById("mappings");
    const clone = template.content.cloneNode(true).firstElementChild;
    clone.querySelector(".delete_button").addEventListener("click", delete_mapping(mapping, clone));
    const sticky_checkbox = clone.querySelector(".sticky_checkbox");
    sticky_checkbox.checked = mapping['sticky'];
    sticky_checkbox.addEventListener("change", sticky_onclick(mapping, sticky_checkbox));
    const scaling_input = clone.querySelector(".scaling_input");
    scaling_input.value = mapping['scaling'] / 1000;
    scaling_input.addEventListener("input", scaling_onchange(mapping, scaling_input));
    const layer_dropdown = clone.querySelector(".layer_dropdown");
    layer_dropdown.value = mapping['layer'];
    layer_dropdown.addEventListener("change", layer_onchange(mapping, layer_dropdown));
    const source_button = clone.querySelector(".source_button");
    source_button.innerText = (mapping['source_usage'] in usages) ? usages[mapping['source_usage']]['name'] : mapping['source_usage'];
    source_button.setAttribute('data-hid-usage', mapping['source_usage']);
    source_button.addEventListener("click", show_usage_modal(mapping, 'source', source_button));
    const target_button = clone.querySelector(".target_button");
    target_button.innerText = (mapping['target_usage'] in usages) ? usages[mapping['target_usage']]['name'] : mapping['target_usage'];
    target_button.setAttribute('data-hid-usage', mapping['target_usage']);
    target_button.addEventListener("click", show_usage_modal(mapping, 'target', target_button));
    container.appendChild(clone);
}

function download_json() {
    clear_error();
    // Always export the multi-profile shape so re-imports preserve every slot.
    const exported = {
        version: CONFIG_VERSION,
        active_profile: device_state.active_profile,
        profiles: device_state.profiles,
    };
    let element = document.createElement('a');
    element.setAttribute('href', 'data:application/json,' + encodeURIComponent(JSON.stringify(exported, null, 4)));
    element.setAttribute('download', 'screen-hopper-config.json');

    element.style.display = 'none';
    document.body.appendChild(element);

    element.click();

    document.body.removeChild(element);
}

function upload_json() {
    clear_error();
    document.getElementById("file_input").click();
}

function file_uploaded() {
    const reader = new FileReader();
    reader.onload = function (e) {
        try {
            const parsed = JSON.parse(e.target.result);
            check_version(parsed['version']);
            // Two accepted shapes: multi-profile { profiles: [...] } or the
            // legacy single-profile JSON (treat as profile 0).
            if (Array.isArray(parsed['profiles'])) {
                if (parsed['profiles'].length === 0) {
                    throw new Error('Imported JSON has no profiles.');
                }
                if (parsed['profiles'].length > NPROFILES) {
                    throw new Error('Imported JSON has ' + parsed['profiles'].length +
                        ' profiles, exceeds maximum of ' + NPROFILES + '.');
                }
                parsed['profiles'].forEach(p => normalize_screen_outputs(p['screens'] || []));
                device_state = {
                    version: CONFIG_VERSION,
                    active_profile: Math.min(parsed['active_profile'] || 0, parsed['profiles'].length - 1),
                    profiles: parsed['profiles'],
                };
            } else {
                normalize_screen_outputs(parsed['screens'] || []);
                device_state = {
                    version: CONFIG_VERSION,
                    active_profile: 0,
                    profiles: [parsed],
                };
            }
            current_profile_idx = device_state.active_profile;
            config = device_state.profiles[current_profile_idx];
            set_ui_state();
        } catch (err) {
            display_error(err);
        }
    };

    const file = document.getElementById("file_input").files[0];
    if (file !== undefined) {
        reader.readAsText(file);
    }

    document.getElementById("file_input").value = '';
}

async function send_feature_command(command, fields = []) {
    let buffer = new ArrayBuffer(CONFIG_SIZE);
    let dataview = new DataView(buffer);
    dataview.setUint8(0, CONFIG_VERSION);
    dataview.setUint8(1, command);
    let pos = 2;
    for (const [type, value] of fields) {
        switch (type) {
            case UINT8:
                dataview.setUint8(pos, value);
                pos += 1;
                break;
            case UINT16:
                dataview.setUint16(pos, value, true);
                pos += 2;
                break;
            case UINT32:
                dataview.setUint32(pos, value, true);
                pos += 4;
                break;
            case INT32:
                dataview.setInt32(pos, value, true);
                pos += 4;
                break;
        }
    }
    add_crc(dataview);

    await device.sendFeatureReport(REPORT_ID_CONFIG, buffer);
}

async function read_config_feature(fields = []) {
    const data_with_report_id = await device.receiveFeatureReport(REPORT_ID_CONFIG);
    const data = new DataView(data_with_report_id.buffer, 1);
    check_crc(data);
    let ret = [];
    let pos = 0;
    for (const type of fields) {
        switch (type) {
            case UINT8:
                ret.push(data.getUint8(pos));
                pos += 1;
                break;
            case UINT16:
                ret.push(data.getUint16(pos, true));
                pos += 2;
                break;
            case UINT32:
                ret.push(data.getUint32(pos, true));
                pos += 4;
                break;
            case INT32:
                ret.push(data.getInt32(pos, true));
                pos += 4;
                break;
        }
    }
    return ret;
}

function clear_error() {
    document.getElementById("error").classList.add("d-none");
}

function display_error(message) {
    document.getElementById("error").innerText = message;
    document.getElementById("error").classList.remove("d-none");
}

function check_crc(data) {
    if (data.getUint32(CONFIG_SIZE - 4, true) != crc32(data, CONFIG_SIZE - 4)) {
        throw new Error('CRC error.');
    }
}

function add_crc(data) {
    data.setUint32(CONFIG_SIZE - 4, crc32(data, CONFIG_SIZE - 4), true);
}

function check_version(config_version) {
    if (config_version != CONFIG_VERSION) {
        throw new Error("Incompatible version: expected " + CONFIG_VERSION + ", got " + config_version + ".");
    }
}

function clear_children(element) {
    while (element.firstChild) {
        element.removeChild(element.firstChild);
    }
}

function delete_mapping(mapping, element) {
    return function () {
        config['mappings'] = config['mappings'].filter(x => x !== mapping);
        // Re-point device_state's profile reference (filter returns a new array).
        device_state.profiles[current_profile_idx]['mappings'] = config['mappings'];
        document.getElementById("mappings").removeChild(element);
    };
}

function sticky_onclick(mapping, element) {
    return function () {
        mapping['sticky'] = element.checked;
    };
}

function scaling_onchange(mapping, element) {
    return function () {
        mapping['scaling'] = element.value === '' ? DEFAULT_SCALING : Math.round(parseFloat(element.value) * 1000);
    };
}

function layer_onchange(mapping, element) {
    return function () {
        mapping['layer'] = element.value;
    };
}

function show_usage_modal(mapping, source_or_target, element) {
    return function () {
        document.querySelector('.usage_modal_title').innerText = "Select " + (source_or_target == 'source' ? "input" : "output");
        document.querySelectorAll('.usage_button').forEach((button) => {
            let clone = button.cloneNode(true);
            button.parentNode.replaceChild(clone, button);
            clone.addEventListener("click", function () {
                let usage = clone.getAttribute('data-hid-usage');
                mapping[source_or_target + '_usage'] = usage;
                element.innerText = usage in usages ? usages[usage]['name'] : usage;
                modal.hide();
            });
        });
        modal.show();
    };
}

function add_mapping_onclick() {
    if (config['mappings'].length >= MAX_MAPPINGS_PER_PROFILE) {
        display_error('Maximum ' + MAX_MAPPINGS_PER_PROFILE + ' mappings per profile.');
        return;
    }
    let new_mapping = {
        'source_usage': '0x00000000',
        'target_usage': '0x00000000',
        'layer': 0,
        'sticky': false,
        'scaling': DEFAULT_SCALING
    };
    config['mappings'].push(new_mapping);
    add_mapping(new_mapping);
}

function setup_usages_modal() {
    let usage_classes = {
        'mouse': document.querySelector('.mouse_usages'),
        'keyboard': document.querySelector('.keyboard_usages'),
        'media': document.querySelector('.media_usages'),
        'other': document.querySelector('.other_usages'),
        'extra': document.querySelector('.extra_usages'),
    };
    for (const [usage_class, element] of Object.entries(usage_classes)) {
        clear_children(element);
    }
    let template = document.getElementById('usage_button_template');
    for (const [usage, usage_def] of Object.entries(usages)) {
        let clone = template.content.cloneNode(true).firstElementChild;
        clone.innerText = usage_def['name'];
        clone.setAttribute('data-hid-usage', usage);
        usage_classes[usage_def['class']].appendChild(clone);
    }
    for (const usage_ of extra_usages) {
        let clone = template.content.cloneNode(true).firstElementChild;
        clone.innerText = usage_;
        clone.setAttribute('data-hid-usage', usage_);
        usage_classes['extra'].appendChild(clone);
    }
}

function partial_scroll_timeout_onchange() {
    let value = document.getElementById('partial_scroll_timeout_input').value;
    if (value === '') {
        value = DEFAULT_PARTIAL_SCROLL_TIMEOUT;
    } else {
        value = Math.round(value * 1000);
    }
    config['partial_scroll_timeout'] = value;
}

function unmapped_passthrough_onchange() {
    config['unmapped_passthrough'] = document.getElementById("unmapped_passthrough_checkbox").checked;
}

function interval_override_onchange() {
    config['interval_override'] = parseInt(document.getElementById("interval_override_dropdown").value, 10);
}

function constraint_mode_onchange() {
    config['constraint_mode'] = parseInt(document.getElementById("constraint_mode_dropdown").value, 10);
}

function offscreen_sensitivity_onchange() {
    let value = document.getElementById('offscreen_sensitivity_input').value;
    if (value === '') {
        value = DEFAULT_SENSITIVITY;
    } else {
        value = Math.round(value * 1000);
    }
    config['offscreen_sensitivity'] = value;
}

function jiggle_interval_onchange() {
    let value = parseInt(document.getElementById('jiggle_interval_input').value, 10);
    if (isNaN(value) || value < 0) {
        value = 0;
    }
    config['jiggle_interval'] = value;
}

function set_screens_ui_state() {
    const container = document.getElementById('screens');
    clear_children(container);
    for (let i = 0; i < config['screens'].length; i++) {
        add_screen_card(i);
    }
    update_screen_count_label();
}

function add_screen_card(index) {
    const template = document.getElementById('screen_template');
    const container = document.getElementById('screens');
    const clone = template.content.cloneNode(true).firstElementChild;
    const screen = config['screens'][index];

    clone.querySelector('.screen_label').textContent = 'Screen ' + index;
    clone.querySelector('.screen_x').value = screen['x'];
    clone.querySelector('.screen_y').value = screen['y'];
    clone.querySelector('.screen_w').value = screen['w'];
    clone.querySelector('.screen_h').value = screen['h'];
    clone.querySelector('.screen_sens').value = screen['sensitivity'] / 1000;
    clone.querySelector('.screen_output').value = screen['output'] || 0;
    // CONFIG_VERSION 11 affine gain: drag_gain = base*1000, drag_curve_k = slope*1e4
    clone.querySelector('.screen_drag_gain').value = (screen['drag_gain'] || 1000) / 1000;
    clone.querySelector('.screen_drag_curve_k').value = (screen['drag_curve_k'] || 0) / 10000;

    clone.querySelectorAll('input, select').forEach(el => {
        el.addEventListener('change', screens_onchange);
    });

    clone.querySelector('.screen_delete').addEventListener('click', function () {
        config['screens'].splice(index, 1);
        set_screens_ui_state();
    });

    container.appendChild(clone);
}

function screens_onchange() {
    const cards = document.querySelectorAll('#screens .screen-card');
    cards.forEach((card, i) => {
        if (i >= config['screens'].length) return;
        config['screens'][i]['x'] = parseInt(card.querySelector('.screen_x').value) || 0;
        config['screens'][i]['y'] = parseInt(card.querySelector('.screen_y').value) || 0;
        config['screens'][i]['w'] = parseInt(card.querySelector('.screen_w').value) || 0;
        config['screens'][i]['h'] = parseInt(card.querySelector('.screen_h').value) || 0;
        let sens = card.querySelector('.screen_sens').value;
        config['screens'][i]['sensitivity'] = sens === '' ? DEFAULT_SENSITIVITY : Math.round(parseFloat(sens) * 1000);
        config['screens'][i]['output'] = parseInt(card.querySelector('.screen_output').value) || 0;
        let dg = card.querySelector('.screen_drag_gain').value;        // base (px/count)
        config['screens'][i]['drag_gain'] = dg === '' ? 1000 : Math.round(parseFloat(dg) * 1000);
        let dck = card.querySelector('.screen_drag_curve_k').value;     // slope (px/count per v)
        config['screens'][i]['drag_curve_k'] = dck === '' ? 0 : Math.round(parseFloat(dck) * 10000);
    });
}

function add_screen_onclick() {
    clear_error();
    if (config['screens'].length >= NSCREENS) {
        display_error('Maximum ' + NSCREENS + ' screens supported.');
        return;
    }
    config['screens'].push({
        'x': 0, 'y': 0, 'w': 0, 'h': 0,
        'sensitivity': 4000, 'output': 0
    });
    set_screens_ui_state();
}

function update_screen_count_label() {
    const label = document.getElementById('screen_count_label');
    label.textContent = config['screens'].length + '/' + NSCREENS;
}

async function detect_displays_onclick() {
    clear_error();
    const status = document.getElementById('detect_status');
    status.textContent = '';
    console.log('[detect] secureContext =', window.isSecureContext,
        '| getScreenDetails =', 'getScreenDetails' in window,
        '| screen.isExtended =', window.screen && window.screen.isExtended);

    if (!('getScreenDetails' in window)) {
        if (window.screen && window.screen.width) {
            layout.displays = [{
                label: 'Display 1',
                w: window.screen.width,
                h: window.screen.height,
                x: 0,
                y: 0,
            }];
            render_layout_ui();
            display_error('This browser lacks the multi-display API (Window Management). ' +
                'Filled in only the current display (' + window.screen.width + 'x' + window.screen.height + '). ' +
                'Use Chrome or Edge for full detection, or add the others manually.');
        } else {
            display_error('This browser does not expose display info. Use Chrome/Edge over https or localhost, ' +
                'or add displays manually with "Add display".');
        }
        return;
    }

    try {
        const details = await window.getScreenDetails();
        if (!details || !details.screens || details.screens.length === 0) {
            display_error('The browser returned no displays.');
            return;
        }
        details.screens.forEach((s, i) => console.log('[detect] screen', i, {
            label: s.label, width: s.width, height: s.height, left: s.left, top: s.top,
            availWidth: s.availWidth, availHeight: s.availHeight,
            devicePixelRatio: s.devicePixelRatio, isPrimary: s.isPrimary, isInternal: s.isInternal,
        }));

        const usable = details.screens.filter(s => s.width > 0 && s.height > 0);
        const skipped = details.screens.length - usable.length;

        if (usable.length === 0) {
            display_error('The browser reported displays but none had a usable size. ' +
                'Enter them manually from System Settings > Displays.');
            return;
        }

        layout.displays = usable.map((s, i) => ({
            label: s.label || ('Display ' + (i + 1)),
            w: s.width,
            h: s.height,
            x: s.left,
            y: s.top,
        }));
        status.textContent = 'Detected ' + usable.length + ' display(s)' +
            (skipped > 0 ? ', skipped ' + skipped + ' with no size' : '') + '.';
        if (skipped > 0) {
            display_error('Note: ' + skipped + ' display(s) came back with no size from the browser ' +
                '(common with mirrored or virtual HiDPI displays). Add or correct them manually below if needed.');
        }
        render_layout_ui();
    } catch (e) {
        console.error('[detect] getScreenDetails failed', e);
        display_error('Display detection failed: ' + (e && e.message ? e.message : e) +
            '. The "window management" permission may be blocked - check the icon at the left of the address bar, ' +
            'then reload and try again. Or add displays manually.');
    }
}

function add_display_onclick() {
    layout.displays.push({
        label: 'Display ' + (layout.displays.length + 1),
        w: 1920, h: 1080, x: 0, y: 0
    });
    render_layout_ui();
}

function render_layout_ui() {
    render_layout_displays();
    document.getElementById('remote_w').value = layout.remote.w;
    document.getElementById('remote_h').value = layout.remote.h;
    document.getElementById('remote_edge').value = layout.remote.edge;
    document.getElementById('edge_resistance_input').value = layout.remote.edge_resistance_px || 0;
    render_layout_preview();
}

function render_layout_displays() {
    const container = document.getElementById('layout_displays');
    clear_children(container);
    layout.displays.forEach((_, i) => add_display_row(i));
}

function add_display_row(index) {
    const template = document.getElementById('display_template');
    const container = document.getElementById('layout_displays');
    const clone = template.content.cloneNode(true).firstElementChild;
    const d = layout.displays[index];

    clone.querySelector('.disp_label').value = d.label;
    clone.querySelector('.disp_w').value = d.w;
    clone.querySelector('.disp_h').value = d.h;
    clone.querySelector('.disp_x').value = d.x;
    clone.querySelector('.disp_y').value = d.y;

    clone.querySelectorAll('input').forEach(el => {
        el.addEventListener('change', layout_displays_onchange);
    });
    clone.querySelector('.disp_delete').addEventListener('click', function () {
        layout.displays.splice(index, 1);
        render_layout_ui();
    });

    container.appendChild(clone);
}

function layout_displays_onchange() {
    const rows = document.querySelectorAll('#layout_displays .layout-display-row');
    rows.forEach((row, i) => {
        if (i >= layout.displays.length) return;
        layout.displays[i] = {
            label: row.querySelector('.disp_label').value,
            w: parseInt(row.querySelector('.disp_w').value) || 0,
            h: parseInt(row.querySelector('.disp_h').value) || 0,
            x: parseInt(row.querySelector('.disp_x').value) || 0,
            y: parseInt(row.querySelector('.disp_y').value) || 0,
        };
    });
    render_layout_preview();
}

function remote_onchange() {
    layout.remote.w = parseInt(document.getElementById('remote_w').value) || 1;
    layout.remote.h = parseInt(document.getElementById('remote_h').value) || 1;
    layout.remote.edge = document.getElementById('remote_edge').value;
    layout.remote.edge_resistance_px = parseInt(document.getElementById('edge_resistance_input').value) || 0;
    render_layout_preview();
}

function pixel_bounding_box(displays) {
    const minX = Math.min(...displays.map(d => d.x));
    const minY = Math.min(...displays.map(d => d.y));
    const maxX = Math.max(...displays.map(d => d.x + d.w));
    const maxY = Math.max(...displays.map(d => d.y + d.h));
    return { minX, minY, maxX, maxY, w: maxX - minX, h: maxY - minY };
}

function remote_pixel_rect(bb) {
    const r = layout.remote;
    if (r.h <= 0 || r.w <= 0) return null;
    if (r.edge === 'left' || r.edge === 'right') {
        const rh = bb.h;
        const rw = rh * r.w / r.h;
        const rx = r.edge === 'left' ? bb.minX - rw : bb.maxX;
        return { x: rx, y: bb.minY, w: rw, h: rh };
    }
    const rw = bb.w;
    const rh = rw * r.h / r.w;
    const ry = r.edge === 'top' ? bb.minY - rh : bb.maxY;
    return { x: bb.minX, y: ry, w: rw, h: rh };
}

function render_layout_preview() {
    const preview = document.getElementById('layout_preview');
    if (!preview) return;
    clear_children(preview);

    if (layout.displays.length === 0) {
        const msg = document.createElement('div');
        msg.className = 'text-muted small p-2';
        msg.textContent = 'No displays yet. Click "Detect displays" or "Add display".';
        preview.appendChild(msg);
        return;
    }

    const bb = pixel_bounding_box(layout.displays);
    const rects = layout.displays.map(d => ({ x: d.x, y: d.y, w: d.w, h: d.h, label: d.label, type: 'local' }));
    const remote = remote_pixel_rect(bb);
    if (remote) {
        rects.push({ x: remote.x, y: remote.y, w: remote.w, h: remote.h, label: 'Remote', type: 'remote' });
    }

    const minX = Math.min(...rects.map(r => r.x));
    const minY = Math.min(...rects.map(r => r.y));
    const maxX = Math.max(...rects.map(r => r.x + r.w));
    const maxY = Math.max(...rects.map(r => r.y + r.h));
    const totalW = maxX - minX;
    const totalH = maxY - minY;
    if (totalW <= 0 || totalH <= 0) return;

    const pad = 10;
    const availW = preview.clientWidth - pad * 2;
    const availH = preview.clientHeight - pad * 2;
    const scale = Math.min(availW / totalW, availH / totalH);
    const offsetX = pad + (availW - totalW * scale) / 2;
    const offsetY = pad + (availH - totalH * scale) / 2;

    for (const r of rects) {
        const box = document.createElement('div');
        box.style.position = 'absolute';
        box.style.left = (offsetX + (r.x - minX) * scale) + 'px';
        box.style.top = (offsetY + (r.y - minY) * scale) + 'px';
        box.style.width = (r.w * scale) + 'px';
        box.style.height = (r.h * scale) + 'px';
        box.style.boxSizing = 'border-box';
        box.style.border = '1px solid #495057';
        box.style.background = r.type === 'remote' ? 'rgba(13,110,253,0.15)' : 'rgba(108,117,125,0.15)';
        box.style.fontSize = '11px';
        box.style.lineHeight = '1.2';
        box.style.overflow = 'hidden';
        box.style.padding = '2px';
        box.style.display = 'flex';
        box.style.alignItems = 'center';
        box.style.justifyContent = 'center';
        box.style.textAlign = 'center';
        box.textContent = r.label + ' (' + Math.round(r.w) + 'x' + Math.round(r.h) + ')';
        preview.appendChild(box);
    }
}

function derive_layout_from_config() {
    const coord_scale = config['coord_scale'];
    const screens = config['screens'] || [];
    if (!coord_scale || coord_scale <= 0 || screens.length === 0) {
        return false;
    }
    const locals = screens.filter(s => (s['output'] || 0) === 0);
    if (locals.length === 0) {
        return false;
    }
    const remote = screens.find(s => (s['output'] || 0) === 1);

    let edge = layout.remote.edge;
    let local_offset_x = 0, local_offset_y = 0;
    let remote_w = layout.remote.w, remote_h = layout.remote.h;
    const edge_resistance_px = Math.round((config['edge_resistance'] || 0) / coord_scale);

    if (remote) {
        const local_min_x = Math.min(...locals.map(s => s['x']));
        const local_min_y = Math.min(...locals.map(s => s['y']));
        const local_max_x = Math.max(...locals.map(s => s['x'] + s['w']));
        const local_max_y = Math.max(...locals.map(s => s['y'] + s['h']));
        const rx = remote['x'], ry = remote['y'], rw = remote['w'], rh = remote['h'];
        if (rx + rw <= local_min_x + 1) {
            edge = 'left'; local_offset_x = rw;
        } else if (rx >= local_max_x - 1) {
            edge = 'right';
        } else if (ry + rh <= local_min_y + 1) {
            edge = 'top'; local_offset_y = rh;
        } else if (ry >= local_max_y - 1) {
            edge = 'bottom';
        }
        const rscale = remote['scale'] || coord_scale;
        remote_w = Math.round(rw / rscale);
        remote_h = Math.round(rh / rscale);
    }

    layout.displays = locals.map((s, i) => ({
        label: 'Display ' + (i + 1),
        w: Math.round(s['w'] / coord_scale),
        h: Math.round(s['h'] / coord_scale),
        x: Math.round((s['x'] - local_offset_x) / coord_scale),
        y: Math.round((s['y'] - local_offset_y) / coord_scale),
    }));
    layout.remote = { w: remote_w, h: remote_h, edge: edge, edge_resistance_px: edge_resistance_px };
    return true;
}

function compute_screens_from_layout() {
    const displays = layout.displays;
    const bb = pixel_bounding_box(displays);
    const coord_scale = 10000000 / Math.max(bb.w, bb.h);
    config['coord_scale'] = Math.trunc(coord_scale);
    config['edge_resistance'] = Math.round((layout.remote.edge_resistance_px || 0) * coord_scale);
    const r = layout.remote;
    const sensitivity = config['offscreen_sensitivity'] || 4000;
    const edge = r.edge;

    let remote_w_scaled, remote_h_scaled, remote_x, remote_y, local_offset_x, local_offset_y;
    if (edge === 'left' || edge === 'right') {
        remote_h_scaled = Math.trunc(bb.h * coord_scale);
        remote_w_scaled = Math.trunc(remote_h_scaled * r.w / r.h);
    } else {
        remote_w_scaled = Math.trunc(bb.w * coord_scale);
        remote_h_scaled = Math.trunc(remote_w_scaled * r.h / r.w);
    }
    if (edge === 'left') {
        remote_x = 0; remote_y = 0; local_offset_x = remote_w_scaled; local_offset_y = 0;
    } else if (edge === 'right') {
        local_offset_x = 0; local_offset_y = 0; remote_x = Math.trunc(bb.w * coord_scale); remote_y = 0;
    } else if (edge === 'top') {
        remote_x = 0; remote_y = 0; local_offset_x = 0; local_offset_y = remote_h_scaled;
    } else {
        local_offset_x = 0; local_offset_y = 0; remote_x = 0; remote_y = Math.trunc(bb.h * coord_scale);
    }

    const remote_scale = r.w ? Math.round(remote_w_scaled / r.w) : Math.round(coord_scale);
    const screens = [{
        x: remote_x, y: remote_y, w: remote_w_scaled, h: remote_h_scaled,
        sensitivity: sensitivity, output: 1, scale: remote_scale
    }];
    for (const d of displays) {
        screens.push({
            x: Math.trunc((d.x - bb.minX) * coord_scale) + local_offset_x,
            y: Math.trunc((d.y - bb.minY) * coord_scale) + local_offset_y,
            w: Math.trunc(d.w * coord_scale),
            h: Math.trunc(d.h * coord_scale),
            sensitivity: sensitivity,
            output: 0,
            scale: Math.round(coord_scale)
        });
    }
    return screens;
}

function apply_layout_onclick() {
    clear_error();
    if (layout.displays.length === 0) {
        display_error('Add at least one local display first.');
        return;
    }
    if (layout.remote.w <= 0 || layout.remote.h <= 0) {
        display_error('Remote machine resolution must be positive.');
        return;
    }
    if (layout.displays.length + 1 > NSCREENS) {
        display_error('Too many displays: ' + layout.displays.length + ' local + 1 remote exceeds ' + NSCREENS + ' screens.');
        return;
    }
    config['screens'] = compute_screens_from_layout();
    set_screens_ui_state();
}

function normalize_screen_outputs(screens) {
    const has_output = screens.some(s => s['output'] !== undefined);
    screens.forEach((s, i) => {
        if (s['output'] === undefined) {
            s['output'] = has_output ? 0 : (i === 0 ? 0 : 1);
        }
    });
}

function load_example(n) {
    const example = structuredClone(examples[n]['config']);
    device_state = {
        version: CONFIG_VERSION,
        active_profile: 0,
        profiles: [example],
    };
    current_profile_idx = 0;
    config = device_state.profiles[0];
    set_ui_state();
}

function setup_examples() {
    const element = document.getElementById("examples");
    const template = document.getElementById("example_template");
    for (let i = 0; i < examples.length; i++) {
        if (i > 0) {
            element.appendChild(document.createTextNode(', '));
        }
        const clone = template.content.cloneNode(true).firstElementChild;
        clone.innerText = examples[i]['description'];
        clone.addEventListener("click", () => load_example(i));
        element.appendChild(clone);
    }
    element.appendChild(document.createTextNode('.'));
}

function hid_on_disconnect(event) {
    if (event.device === device) {
        device = null;
        document.getElementById("load_from_device").disabled = true;
        document.getElementById("save_to_device").disabled = true;
    }
}
