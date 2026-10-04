"""Generate a bookmarklet for auto-filling Greenhouse job applications.

Run this to produce a JavaScript bookmarklet URL that can be:
1. Saved as a browser bookmark
2. Pasted into the browser console on any Greenhouse application page

The bookmarklet fills standard fields, react-select dropdowns, and
pauses before submit for review.
"""
from __future__ import annotations

from pathlib import Path

from src.apply.profile import load_profile


def generate_fill_js(profile: dict) -> str:
    """Generate the JavaScript that fills Greenhouse forms."""
    p = profile["personal"]
    links = profile["links"]
    auth = profile["work_authorization"]
    answers = profile["standard_answers"]

    return f"""
(function() {{
    'use strict';

    const PROFILE = {{
        first_name: '{p["first_name"]}',
        last_name: '{p["last_name"]}',
        email: '{p["email"]}',
        phone: '{p["phone"]}',
        location: '{p["location"]}',
        linkedin: '{links["linkedin"]}',
        sponsorship_required: '{auth["sponsorship_required"]}',
        authorized_us: '{auth["authorized_us"]}',
        how_hear: '{answers["how_hear_about_job"]}',
    }};

    /* --- Helper: fill a text input by id --- */
    function fillById(id, value) {{
        if (!value) return false;
        const el = document.getElementById(id);
        if (!el || el.value.trim()) return false; // skip if already filled
        const setter = Object.getOwnPropertyDescriptor(
            HTMLInputElement.prototype, 'value'
        ).set;
        setter.call(el, value);
        el.dispatchEvent(new Event('input', {{bubbles: true}}));
        el.dispatchEvent(new Event('change', {{bubbles: true}}));
        return true;
    }}

    /* --- Helper: fill react-select by typing + selecting first match --- */
    async function fillReactSelect(inputId, searchText) {{
        const el = document.getElementById(inputId);
        if (!el) return false;
        // Check if already has a value (not "Select...")
        const control = el.closest('[class*="select"]');
        const current = control?.querySelector('[class*="singleValue"]');
        if (current) return false; // already selected

        el.focus();
        const setter = Object.getOwnPropertyDescriptor(
            HTMLInputElement.prototype, 'value'
        ).set;
        setter.call(el, searchText);
        el.dispatchEvent(new Event('input', {{bubbles: true}}));

        await new Promise(r => setTimeout(r, 400));

        const menu = control?.querySelector('[class*="menu"]');
        if (!menu) return false;
        const option = menu.querySelector('[class*="option"]');
        if (!option) return false;
        option.click();
        return true;
    }}

    /* --- Main fill logic --- */
    async function fillForm() {{
        let filled = 0;
        let skipped = 0;

        // Standard text fields
        if (fillById('first_name', PROFILE.first_name)) filled++;
        if (fillById('last_name', PROFILE.last_name)) filled++;
        if (fillById('email', PROFILE.email)) filled++;
        if (fillById('phone', PROFILE.phone)) filled++;

        // Location (react-select)
        if (await fillReactSelect('candidate-location', PROFILE.location)) filled++;

        // Custom questions - scan labels to match
        const fields = document.querySelectorAll('input[id^="question_"]');
        for (const el of fields) {{
            const fieldDiv = el.closest('.field');
            if (!fieldDiv) continue;
            const label = fieldDiv.querySelector('label')?.textContent?.toLowerCase() || '';

            if (label.includes('linkedin') && !el.value.trim()) {{
                const setter = Object.getOwnPropertyDescriptor(
                    HTMLInputElement.prototype, 'value'
                ).set;
                setter.call(el, PROFILE.linkedin);
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                filled++;
            }} else if (label.includes('how did you hear about') && !label.includes('this job') && !el.value.trim()) {{
                const setter = Object.getOwnPropertyDescriptor(
                    HTMLInputElement.prototype, 'value'
                ).set;
                setter.call(el, PROFILE.how_hear);
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                filled++;
            }}
        }}

        // React-select dropdowns
        const comboboxes = document.querySelectorAll('input[role="combobox"]');
        for (const el of comboboxes) {{
            const container = el.closest('.field') || el.closest('.select');
            const label = container?.querySelector('label')?.textContent?.toLowerCase() || '';

            if (label.includes('how did you hear about this job')) {{
                // Try to find an option with "website" in name
                await fillReactSelect(el.id, 'Website');
                filled++;
            }} else if (label.includes('sponsor') || label.includes('visa')) {{
                await fillReactSelect(el.id, PROFILE.sponsorship_required);
                filled++;
            }} else if (label.includes('authorized to work') || label.includes('lawfully authorized')) {{
                await fillReactSelect(el.id, PROFILE.authorized_us);
                filled++;
            }} else if (label.includes('able to meet') || label.includes('requirement')) {{
                await fillReactSelect(el.id, 'Yes');
                filled++;
            }} else if (label.includes('gender') || label.includes('race') || label.includes('ethnicity') || label.includes('veteran') || label.includes('disability')) {{
                await fillReactSelect(el.id, 'Decline');
                filled++;
            }}
        }}

        // Show summary
        const msg = `Auto-fill complete!\\n\\nFilled: ${{filled}} fields\\n\\nPlease review all fields before submitting.\\nCheck sponsorship and work auth answers carefully.`;
        alert(msg);
    }}

    fillForm().catch(err => alert('Auto-fill error: ' + err.message));
}})();
"""


def generate_bookmarklet(profile: dict) -> str:
    """Generate a bookmarklet URL from the fill script."""
    js = generate_fill_js(profile)
    # Minify: remove newlines, compress whitespace
    lines = [line.strip() for line in js.strip().split("\n") if line.strip()]
    minified = " ".join(lines)
    return f"javascript:{minified}"


def main():
    profile = load_profile()
    js = generate_fill_js(profile)
    bookmarklet = generate_bookmarklet(profile)

    # Write the full JS for console use
    output_dir = Path(__file__).resolve().parent.parent.parent / "output"
    output_dir.mkdir(exist_ok=True)

    js_path = output_dir / "autofill.js"
    js_path.write_text(js, encoding="utf-8")
    print(f"Full JS written to: {js_path}")

    bookmarklet_path = output_dir / "autofill_bookmarklet.txt"
    bookmarklet_path.write_text(bookmarklet, encoding="utf-8")
    print(f"Bookmarklet URL written to: {bookmarklet_path}")

    print(f"\nTo use:")
    print(f"  1. Open a Greenhouse job application page")
    print(f"  2. Open browser console (Cmd+Option+J)")
    print(f"  3. Paste the contents of {js_path}")
    print(f"  4. Review all fields before submitting")
    print(f"\nOr create a bookmark with the URL from {bookmarklet_path}")


if __name__ == "__main__":
    main()
