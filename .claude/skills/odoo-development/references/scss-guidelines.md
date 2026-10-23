# CSS and SCSS Guidelines

CSS/SCSS formatting, naming conventions, and variable patterns for Odoo 19.

## Syntax and Formatting

```scss
.o_foo, .o_foo_bar, .o_baz {
    height: $o-statusbar-height;

    .o_qux {
        height: $o-statusbar-height * 0.5;
    }
}

.o_corge {
    background: $o-list-footer-bg-color;
}
```

### Rules

- **4 space indentation**, no tabs
- **Max 80 characters** per line
- Opening brace `{`: space after last selector
- Closing brace `}`: on its own line
- One declaration per line
- Meaningful use of whitespace

## Properties Order

Order properties from "outside" in, starting with `position` and ending with decorative rules:

```scss
.o_element {
    // 1. Scoped SCSS variables (top, with empty line after)
    $-inner-gap: $border-width + $legend-margin-bottom;

    // 2. CSS variables
    --element-margin: 1rem;
    --element-size: 3rem;

    // 3. Position and display
    @include o-position-absolute(1rem);
    display: block;

    // 4. Box model (margin, width, border, padding)
    margin: var(--element-margin);
    width: calc(var(--element-size) + #{$-inner-gap});
    border: 0;
    padding: 1rem;

    // 5. Visual (background, color)
    background: blue;

    // 6. Typography and decorative
    font-size: 1rem;
    filter: blur(2px);
}
```

## Naming Conventions

### Class Names

- Avoid `id` selectors
- Prefix classes with `o_<module_name>` (e.g., `o_sale`, `o_crewradar`)
- Webclient uses just `o_` prefix
- Use "Grandchild" approach for nested elements (avoid hyper-specific names)

```html
<!-- Bad: hyper-specific names -->
<div class="o_element_wrapper">
    <div class="o_element_wrapper_entries">
        <span class="o_element_wrapper_entries_entry">
            <a class="o_element_wrapper_entries_entry_link">Entry</a>
        </span>
    </div>
</div>

<!-- Good: grandchild approach -->
<div class="o_element_wrapper">
    <div class="o_element_entries">
        <span class="o_element_entry">
            <a class="o_element_link">Entry</a>
        </span>
    </div>
</div>
```

### SCSS Variables

Pattern: `$o-[root]-[element]-[property]-[modifier]`

| Part | Description |
|------|-------------|
| `$o-` | Prefix |
| `[root]` | Component or module name |
| `[element]` | Optional inner element identifier |
| `[property]` | Property/behavior defined |
| `[modifier]` | Optional modifier |

```scss
$o-block-color: value;
$o-block-title-color: value;
$o-block-title-color-hover: value;
```

### Scoped SCSS Variables

Variables declared within blocks, not accessible outside. Pattern: `$-[variable name]`

```scss
.o_element {
    $-inner-gap: compute-something;

    margin-right: $-inner-gap;

    .o_element_child {
        margin-right: $-inner-gap * 0.5;
    }
}
```

### CSS Variables

Pattern (BEM): `--[root]__[element]-[property]--[modifier]`

```scss
.o_kanban_record {
    --KanbanRecord-width: value;
    --KanbanRecord__picture-border: value;
    --KanbanRecord__picture-border--active: value;
}

// Adapt component in different context
.o_form_view {
    --KanbanRecord-width: another-value;
}
```

### Mixins and Functions

Pattern: `o-[name]`, with verbs in imperative form for functions (get, make, apply).

```scss
@mixin o-avatar($-size: 1.5em, $-radius: 100%) {
    width: $-size;
    height: $-size;
    border-radius: $-radius;
}

@function o-invert-color($-color, $-amount: 100%) {
    $-inverse: change-color($-color, $-hue: hue($-color) + 180);
    @return mix($-inverse, $-color, $-amount);
}
```

## CSS Variables Usage

Use CSS variables for **contextual adaptations**, not global design system:

```scss
// component.scss - define with fallback
.o_MyComponent {
    color: var(--MyComponent-color, #313131);
}

// dashboard.scss - override in context
.o_MyDashboard {
    --MyComponent-color: #017e84;
}
```

### SCSS vs CSS Variables

| SCSS Variables | CSS Variables |
|---------------|---------------|
| Imperative, compiled away | Declarative, in final output |
| Global design system | Contextual adaptations |
| `$o-component-color` | `--MyComponent-color` |

Combine both: SCSS for design system, CSS for runtime context:

```scss
// secondary_variables.scss
$o-component-color: $o-main-text-color;
$o-dashboard-color: $o-info;

// component.scss
.o_component {
    color: var(--MyComponent-color, #{$o-component-color});
}

// dashboard.scss
.o_dashboard {
    --MyComponent-color: #{$o-dashboard-color};
}
```

A Sass function or variable inside a CSS custom property is not evaluated:
`--X: rgba($black, 0.1)` reaches the browser as literal text, and the browser
drops the property that reads it. Interpolate (`--X: #{rgba($black, 0.1)}`) or
write plain CSS (`rgba(0, 0, 0, 0.1)`). Seen 23-Oct-2026 in `oteny_home`: the
icon tiles of the home screen had no shadow at all.

### Avoid `:root`

Don't define CSS variables on `:root` - Odoo handles global design via SCSS. Exceptions: templates shared across bundles needing contextual awareness.

## Dark Mode in Odoo

Odoo loads dark mode SCSS files conditionally. **Don't use** `@media (prefers-color-scheme: dark)`.

```python
# __manifest__.py
'assets': {
    'web.assets_backend': [
        'crewradar/static/src/scss/planning.scss',  # Light mode
        ('after', 'web/static/src/scss/primary_variables.dark.scss',
         'crewradar/static/src/scss/planning.dark.scss'),  # Dark mode
    ],
}
```

Create separate `.dark.scss` files that override variables:

```scss
// planning.scss (light/default)
.o_crewradar_planning {
    --planning-bg: #ffffff;
    --planning-border: #dee2e6;
}

// planning.dark.scss (dark mode overrides)
.o_crewradar_planning {
    --planning-bg: #1e1e1e;
    --planning-border: #404040;
}
```

## Static File Organization

```
static/
├── lib/                    # External JS libraries
│   └── external_lib/
├── src/
│   ├── css/               # Plain CSS (rare)
│   ├── js/
│   │   └── tours/         # End user tours (tutorials)
│   ├── scss/              # SCSS files
│   └── xml/               # QWeb templates for JS
├── img/                   # Images
└── tests/
    └── tours/             # Tour test files
```

Each JS component in its own file with meaningful name. Same for SCSS and static XML.

## Troubleshooting: Style Compilation Error in Debug Mode

When using `?debug=1`, Odoo may intermittently show:

> *Style error. The style compilation failed. This is an administrator or developer error that must be fixed for the entire database before continuing working.*

With the browser console warning: `Could not get content for base/static/src/scss/res_partner.scss` (or similar standard Odoo files).

**Cause**: The CSS asset attachment (`ir_attachment`) got corrupted during a transient compilation failure. The bare `except` clause in `assetsbundle.py` `_fetch_content` swallows the real exception. Without debug mode, the cached minified `.min.css` is served instead, so the error is invisible.

**Fixes** (in order of convenience):
1. **Restart the Odoo server** — regenerates asset bundles on next request
2. **Debug menu → Regenerate Assets Bundles** (requires `?debug=assets`)
3. **SQL cleanup**: `DELETE FROM ir_attachment WHERE name LIKE 'web.assets%' AND file_size < 1000 AND name LIKE '%.css';`

This is an Odoo framework quirk, not a custom module bug.
