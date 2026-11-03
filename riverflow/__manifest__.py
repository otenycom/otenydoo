{
    "name": "Riverflow",
    "version": "19.0.1.1389",
    "depends": ["base", "mail", "oteny_shortcut", "oteny_bot"],
    "author": "Vriend Studio",
    "category": "Workflow",
    "description": """
    Task manager for the Service Industry
    """,
    # data files always loaded at installation
    "data": [
        "security/riverflow_security.xml",
        "security/ir.model.access.csv",
        "data/riverflow_teams_data.xml",
        "wizards/riverflow_transition_wizard_view.xml",
        "wizards/riverflow_service_wizard_view.xml",
        "wizards/riverflow_start_service_view.xml",
        "wizards/riverflow_service_email_sender_view.xml",
        "wizards/riverflow_service_register_wizard_view.xml",
        "wizards/riverflow_supplier_confirmed_view.xml",
        "wizards/riverflow_enter_train_ticket_view.xml",
        "wizards/riverflow_enter_airline_ticket_view.xml",
        "wizards/riverflow_enter_distance_wizard_view.xml",
        "wizards/riverflow_set_deadline_tomorrow_wizard_view.xml",
        "wizards/riverflow_verify_trip_locations_wizard_view.xml",
        "views/riverflow_service_views.xml",
        "views/riverflow_workflow_views.xml",
        "views/riverflow_state_views.xml",
        "views/riverflow_transition_views.xml",
        "views/riverflow_transition_action_views.xml",
        "views/riverflow_state_record_views.xml",
        "views/riverflow_team_views.xml",
        "views/riverflow_named_domain_views.xml",
        "views/riverflow_auto_add_service_views.xml",
        "views/riverflow_check_result_views.xml",
        "views/res_config_settings_views.xml",
        "data/transition_action_data.xml",
        "data/workflow_service_task.xml",
        "data/workflow_service_review.xml",
        "data/service_review_email_bounce_template.xml",
        "data/service_email_workflow.xml",
        "data/service_send_notification_workflow.xml",
        "data/service_arrange_and_send_workflow.xml",
        # "data/riverflow_back_office_workflow.xml",
        "data/riverflow_taxi_order_workflow.xml",
        "data/riverflow_taxi_leg_workflow.xml",
        "data/riverflow_train_ticket_workflow.xml",
        "data/ir_config_parameter_data.xml",
        "data/riverflow_bot_cron.xml",
    ],
    "assets": {
        # Before owl.js, which captures requestAnimationFrame when it loads: on a
        # local development server a hidden page keeps drawing (see the file).
        "web._assets_core": [
            ("prepend", "riverflow/static/src/core/hidden_page_animation_frames.js"),
        ],
        "web.assets_backend": [
            "riverflow/static/src/components/**/*",
            # Dark mode files load via web.assets_web_dark only
            (
                "remove",
                "riverflow/static/src/components/timeline/timeline_scale_selector.dark.scss",
            ),
            "riverflow/static/src/patch/**/*",
            "riverflow/static/src/riverflow_force_xl.js",
            "riverflow/static/src/scss/bg-decorations.scss",
            "riverflow/static/src/scss/form_fields.scss",
        ],
        "web.assets_web_dark": [
            "riverflow/static/src/components/timeline/timeline_scale_selector.dark.scss",
        ],
        "web.assets_unit_tests": [
            "riverflow/static/tests/**/*",
            ("remove", "riverflow/static/tests/tours/**/*"),
        ],
        "web.assets_tests": [
            "riverflow/static/tests/tours/**/*",
        ],
    },
    "application": True,
    "auto_install": False,
    "license": "LGPL-3",
}
