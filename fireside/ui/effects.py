"""Effects and filters: a ten-band equalizer and picture adjustments."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk  # noqa: E402

from ..player import EQ_FREQS, EQ_PRESETS  # noqa: E402

PICTURE = (
    ('brightness', 'Brightness', -1.0, 1.0, 0.0),
    ('contrast', 'Contrast', 0.0, 2.0, 1.0),
    ('saturation', 'Saturation', 0.0, 2.0, 1.0),
    ('hue', 'Hue', -1.0, 1.0, 0.0),
)


class EffectsDialog(Adw.Dialog):
    def __init__(self, settings):
        super().__init__(title='Effects and Filters', content_width=620)
        self.settings = settings
        self._quiet = False

        stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher(stack=stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header = Adw.HeaderBar(title_widget=switcher)
        view = Adw.ToolbarView(content=stack)
        view.add_top_bar(header)
        self.set_child(view)

        stack.add_titled_with_icon(self._audio_page(), 'audio', 'Equalizer', 'fireside-equalizer-symbolic')
        stack.add_titled_with_icon(self._picture_page(), 'picture', 'Picture', 'camera-photo-symbolic')

    # ---- audio ---------------------------------------------------------------------------

    def _audio_page(self):
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        self.eq_on = Adw.SwitchRow(title='Equalizer', subtitle='Shape the sound with ten bands',
                                   active=self.settings['eq_on'])
        self.eq_on.connect('notify::active', lambda r, _p: self.settings.set('eq_on', r.get_active()))
        group.add(self.eq_on)
        ids = list(EQ_PRESETS)
        self.preset_ids = ids + ['custom']
        names = Gtk.StringList.new([EQ_PRESETS[i][0] for i in ids] + ['Custom'])
        self.preset = Adw.ComboRow(title='Preset', model=names)
        cur = self.settings['eq_preset']
        self.preset.set_selected(self.preset_ids.index(cur) if cur in self.preset_ids else len(ids))
        self.preset.connect('notify::selected', self._preset_chosen)
        group.add(self.preset)
        page.add(group)

        bands_group = Adw.PreferencesGroup()
        bands = Gtk.Box(spacing=4, homogeneous=True, margin_top=8, margin_bottom=8)
        self.band_scales = []
        for i, freq in enumerate(EQ_FREQS):
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            col.add_css_class('eq-band')
            val = Gtk.Label(label='0')
            scale = Gtk.Scale(orientation=Gtk.Orientation.VERTICAL, inverted=True, draw_value=False,
                              vexpand=True, halign=Gtk.Align.CENTER)
            scale.set_range(-12, 12)
            scale.set_increments(0.5, 3)
            scale.add_mark(0, Gtk.PositionType.RIGHT, None)
            scale.set_value(self.settings['eq_bands'][i])
            scale.set_tooltip_text(f'{freq}Hz')
            scale.connect('value-changed', self._band_moved, i, val)
            self._band_label(val, scale.get_value())
            col.append(val)
            col.append(scale)
            col.append(Gtk.Label(label=freq))
            bands.append(col)
            self.band_scales.append(scale)
        card = Gtk.Box(css_classes=['card'])
        bands.set_margin_start(10)
        bands.set_margin_end(10)
        bands.set_hexpand(True)
        card.append(bands)
        bands_group.add(card)
        page.add(bands_group)
        return page

    def _band_label(self, label, value):
        label.set_label(f'{value:+.0f}' if abs(value) >= 0.5 else '0')

    def _band_moved(self, scale, index, label):
        self._band_label(label, scale.get_value())
        if self._quiet:
            return
        bands = list(self.settings['eq_bands'])
        bands[index] = round(scale.get_value(), 1)
        self.settings.set('eq_bands', bands)
        if not self.settings['eq_on']:
            self.eq_on.set_active(True)
        if self.settings['eq_preset'] != 'custom':
            self._quiet = True
            self.preset.set_selected(len(self.preset_ids) - 1)
            self._quiet = False
            self.settings.set('eq_preset', 'custom')

    def _preset_chosen(self, row, _p):
        if self._quiet:
            return
        pid = self.preset_ids[row.get_selected()]
        self.settings.set('eq_preset', pid)
        if pid == 'custom':
            return
        values = EQ_PRESETS[pid][1]
        self._quiet = True
        for scale, v in zip(self.band_scales, values):
            scale.set_value(v)
        self._quiet = False
        self.settings.set('eq_bands', [float(v) for v in values])
        if pid != 'flat' and not self.settings['eq_on']:
            self.eq_on.set_active(True)

    # ---- picture -------------------------------------------------------------------------

    def _picture_page(self):
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(description='Changes apply to video while it plays and are kept for next time.')
        self.pic_rows = {}
        for key, title, lo, hi, default in PICTURE:
            row = Adw.ActionRow(title=title)
            scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, draw_value=False, hexpand=True,
                              width_request=260, valign=Gtk.Align.CENTER)
            scale.set_range(lo, hi)
            scale.set_increments(0.02, 0.1)
            scale.add_mark(default, Gtk.PositionType.BOTTOM, None)
            scale.set_value(self.settings[key])
            scale.connect('value-changed', lambda s, k: self.settings.set(k, round(s.get_value(), 3)), key)
            row.add_suffix(scale)
            group.add(row)
            self.pic_rows[key] = scale
        page.add(group)
        reset_group = Adw.PreferencesGroup()
        reset = Adw.ButtonRow(title='Reset picture')
        reset.connect('activated', self._reset_picture)
        reset_group.add(reset)
        page.add(reset_group)
        return page

    def _reset_picture(self, *_):
        for key, _t, _lo, _hi, default in PICTURE:
            self.pic_rows[key].set_value(default)
