#!/usr/bin/env python3

import os
import i18n

from PySide6.QtCore import QLocale

from subtitld.modules.session import PATH_SUBTITLD, PATH_LOCALE

FALLBACK_LANGUAGE = 'en_US'

i18n.set('file_format', 'json')
i18n.load_path.append(os.path.join(PATH_SUBTITLD, 'locale'))
i18n.set('filename_format', '{locale}.{format}')
i18n.set('skip_locale_root_data', True)
i18n.set('fallback', FALLBACK_LANGUAGE)
i18n.set('locale', FALLBACK_LANGUAGE)


def _load(language):
    if language not in i18n.translations.container:
        i18n.resource_loader.load_translation_file(f'{language}.json', str(PATH_LOCALE), language)


_load(FALLBACK_LANGUAGE)


def _(text):
    # Asked for a key its locale lacks, python-i18n lists the locale folder
    # and parses the whole locale file again, on every call, before falling
    # back: ~0.3 ms a lookup, some of them in paint events. Both files are
    # loaded once (here and in set_language), so look in them directly.
    for locale in (i18n.get('locale'), FALLBACK_LANGUAGE):
        if i18n.translations.has(text, locale):
            return i18n.t(text, locale=locale)
    return text


# LIST_OF_MONTHS = ['', _('January'), _('February'), _('March'), _('April'), _('May'), _('June'), _('July'), _('August'), _('September'), _('October'), _('November'), _('December')]
# LIST_OF_WEEKDAYS = [_('Monday'), _('Tuesday'), _('Wednesday'), _('Thursday'), _('Friday'), _('Saturday'), _('Sunday')]

# def translate_month_names():
#     global LIST_OF_MONTHS
#     LIST_OF_MONTHS = []
#     for item in ['', _('January'), _('February'), _('March'), _('April'), _('May'), _('June'), _('July'), _('August'), _('September'), _('October'), _('November'), _('December')]:
#         LIST_OF_MONTHS.append(_(item))


def load_translation_files():
    for lp in i18n.load_path:
        for f in os.listdir(lp):
            path = os.path.join(lp, f)
            if os.path.isfile(path) and path.endswith(i18n.config.get('file_format')):
                locale = f.split(i18n.config.get('namespace_delimiter'))[0]
                if '{locale}' in i18n.config.get('filename_format') and locale not in i18n.config.get('available_locales'):
                    i18n.resource_loader.load_translation_file(f, lp, locale)


# def get_list_of_months(language):
#     month_list = ['']
#     for month in LIST_OF_MONTHS:
#         if month:
#             month_list.append(i18n.t(month, locale=language))
#     return month_list


# def get_list_of_weekdays(language):
#     weekdays = []
#     for month in LIST_OF_WEEKDAYS:
#         if month:
#             weekdays.append(i18n.t(month, locale=language))
#     return weekdays


def set_language(language):
    """Translate into `language` (a locale file's name, like 'pt_BR') from
    now on; en_US if there is no file for it. Widgets built before keep their
    texts until their translate() runs."""
    if language not in get_available_language_names():
        language = FALLBACK_LANGUAGE
    _load(language)
    i18n.set('locale', language)


def get_language():
    return i18n.get('locale')


def system_language(ui_languages=None):
    """The language to use when none was picked: the first of the system's
    preferred interface languages there is a locale file for, the same
    language from another country counting (pt_PT gets pt_BR); en_US if
    none. `ui_languages` is BCP 47 tags, best first, and defaults to the
    system's (LANGUAGE, then LC_ALL / LC_MESSAGES / LANG on Linux)."""
    available = get_available_language_names()
    if ui_languages is None:
        ui_languages = QLocale.system().uiLanguages()
    for tag in ui_languages:
        name = QLocale(tag).name()  # 'pt-Latn-BR' -> 'pt_BR'
        if name in available:
            return name
        for code in available:
            if code.split('_')[0] == name.split('_')[0]:
                return code
    return FALLBACK_LANGUAGE


def pick_language(saved=None, ui_languages=None):
    """The language to start in: the one saved in the settings if there is a
    file for it, else the system's."""
    if saved in get_available_language_names():
        return saved
    return system_language(ui_languages)


def get_language_pairs(language):
    result = i18n.translations.container.get(language, {})
    return result


def get_available_language_names():
    """Every language there is a locale file for, by its name in itself:
    {'en_US': 'English', 'pt_BR': 'Português (Brasil)'}."""
    final_dict = {}
    for path in sorted(PATH_LOCALE.glob('*.json')):
        _load(path.stem)
        final_dict[path.stem] = i18n.translations.container[path.stem].get('language_name') or path.stem
    return final_dict
