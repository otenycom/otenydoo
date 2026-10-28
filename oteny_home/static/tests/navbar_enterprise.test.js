import { after, describe, expect, test } from "@odoo/hoot";
import { getTemplate, registerTemplate } from "@web/core/templates";

// Odoo Enterprise (web_enterprise, which odoo.sh installs on every database)
// builds its top bar as a primary copy of web.NavBar and removes the name of
// the open app from it with this xpath. The top bar of oteny_home must leave
// that element in web.NavBar, or the web client does not start on Enterprise.

describe.current.tags("desktop");

test("a primary copy of web.NavBar can remove the app name as web_enterprise does", async () => {
    const unregister = registerTemplate(
        "oteny_home.test_enterprise_navbar",
        "/web_enterprise/static/src/webclient/navbar/navbar.xml",
        `<t t-name="oteny_home.test_enterprise_navbar" t-inherit="web.NavBar" t-inherit-mode="primary">
            <xpath expr="//DropdownItem[@t-esc='currentApp.name']" position="replace"/>
        </t>`
    );
    after(unregister);

    const template = getTemplate("oteny_home.test_enterprise_navbar");
    expect(template.querySelector("DropdownItem[t-esc='currentApp.name']")).toBe(null);
});
