# UI bug — unexpected `value` field during router association

Status: OPEN
Area: Customer → Devices → New device / association workflow

## Observed behavior

When creating/associating a router from the customer Device workflow, the UI unexpectedly asks the operator to provide a field named `value`.

That field has no product meaning in the Device onboarding flow and was not requested by the operator.

## Current intended MikroTik flow

The current `device_new.html` form exposes these meaningful fields:

- vendor;
- device type;
- optional operator alias;
- optional Site;
- vendor-specific association fields only when applicable.

For MikroTik, onboarding is outbound and should not require manually entering identity, model, serial, firmware or an unexplained generic `value` field. After Device creation, NSM should generate/continue the RouterOS one-shot enrollment flow.

The backend `add_device()` handler currently declares vendor/device type/display name/site and the known vendor-specific inventory fields. It does not define a required `value` parameter.

Therefore an operator-visible required `value` prompt is unintended and must not be accepted as part of the workflow.

## Investigation targets

- verify the submitted form action still resolves to `POST /customers/{customer_id}/devices`;
- inspect whether a recently added route or generic form helper is intercepting the request;
- inspect rendered HTML/DOM for an injected or malformed input named/labelled `value`;
- verify browser validation is not targeting a hidden/inactive vendor field;
- verify inactive vendor-specific inputs are disabled or otherwise cannot become validation targets;
- verify FastAPI validation errors are mapped back to meaningful onboarding fields rather than leaking an internal parameter name into the UI.

## Expected behavior

For a MikroTik router:

1. choose `MikroTik`;
2. choose `Router`;
3. optionally set alias and Site;
4. click `Crea e continua onboarding`;
5. Device is created as pending enrollment;
6. the guided RouterOS onboarding/token flow is shown.

No generic `value` input/prompt should ever appear.

## Acceptance criteria

- [ ] No field/prompt named `value` is displayed during MikroTik Device creation/association.
- [ ] MikroTik creation succeeds with only the intended required fields.
- [ ] Optional alias/Site remain optional.
- [ ] Inactive Ubiquiti/TR-069/Generic inputs do not block MikroTik submission.
- [ ] Ubiquiti still requires MAC through the intended validation path.
- [ ] TR-069 still requires MAC or serial through the intended validation path.
- [ ] Validation errors are shown with product-facing field names/messages.
- [ ] Successful MikroTik creation continues into the one-shot enrollment workflow.
- [ ] Add a regression test covering MikroTik form submission without any `value` field.

## Scope boundary

This PR is limited to the unexpected `value` requirement in Device onboarding/association. It does not change the RouterOS enrollment protocol itself.
