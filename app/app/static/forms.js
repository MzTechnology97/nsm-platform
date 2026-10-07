/*
 * Page behaviours driven by data-* attributes.
 *
 * The production Content-Security-Policy (default-src 'self') blocks inline
 * <script> blocks and on* attributes, so every page behaviour lives here.
 */
(function () {
  'use strict';

  // Confirmations: <form data-confirm="..."> and <button data-confirm="...">.
  document.addEventListener('submit', (event) => {
    const form = event.target;
    const message = form.dataset ? form.dataset.confirm : null;
    if (message && !window.confirm(message)) event.preventDefault();
  });
  document.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-confirm], a[data-confirm]');
    if (button && !window.confirm(button.dataset.confirm)) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  // Copy to clipboard: <button data-copy-target="element-id">.
  document.querySelectorAll('[data-copy-target]').forEach((button) => {
    button.addEventListener('click', () => {
      const source = document.getElementById(button.dataset.copyTarget);
      if (!source || !navigator.clipboard) return;
      navigator.clipboard.writeText(source.innerText).then(() => {
        const label = button.textContent;
        button.textContent = 'Copiato';
        window.setTimeout(() => { button.textContent = label; }, 1500);
      });
    });
  });

  // Clickable rows: <tr data-href="/path"> (links and buttons keep their own action).
  document.querySelectorAll('[data-href]').forEach((row) => {
    row.addEventListener('click', (event) => {
      if (event.target.closest('a, button, input, select, label')) return;
      window.location.href = row.dataset.href;
    });
  });

  // New device: show the association fields of the selected vendor only and
  // disable the others, so hidden inputs with the same name are not submitted.
  const vendorSelect = document.getElementById('vendor-select');
  if (vendorSelect) {
    const update = () => {
      document.querySelectorAll('[data-vendor]').forEach((section) => {
        const active = section.dataset.vendor === vendorSelect.value;
        section.classList.toggle('hidden', !active);
        section.querySelectorAll('input, select, textarea').forEach((field) => { field.disabled = !active; });
      });
    };
    vendorSelect.addEventListener('change', update);
    update();
  }

  // Device management: only sites of the selected customer.
  const manageCustomer = document.getElementById('manage-customer');
  const manageSite = document.getElementById('manage-site');
  if (manageCustomer && manageSite) {
    const update = () => {
      let selectedVisible = false;
      Array.from(manageSite.options).forEach((option, index) => {
        if (index === 0) { option.hidden = false; return; }
        const show = option.dataset.customer === manageCustomer.value;
        option.hidden = !show;
        if (show && option.selected) selectedVisible = true;
      });
      if (!selectedVisible) manageSite.value = '';
    };
    manageCustomer.addEventListener('change', update);
    update();
  }

  // Bulk device actions on the customer device list.
  const bulk = document.querySelector('[data-device-bulk-form]');
  if (bulk) {
    const action = bulk.querySelector('[data-bulk-action]');
    const customer = bulk.querySelector('[data-target-customer]');
    const site = bulk.querySelector('[data-target-site]');
    const all = bulk.querySelector('[data-select-all]');
    const fields = () => bulk.querySelectorAll('[data-move-field]').forEach((e) => e.classList.toggle('hidden', action.value !== 'move'));
    const sites = () => {
      const id = customer.value;
      Array.from(site.options).forEach((option, index) => {
        if (index === 0) return;
        option.hidden = option.dataset.customer !== id;
        if (option.hidden && option.selected) site.value = '';
      });
    };
    action.addEventListener('change', fields);
    customer.addEventListener('change', sites);
    if (all) all.addEventListener('change', () => bulk.querySelectorAll('[data-device-check]').forEach((x) => { x.checked = all.checked; }));
    bulk.addEventListener('submit', (event) => {
      const count = bulk.querySelectorAll('[data-device-check]:checked').length;
      if (!count) { event.preventDefault(); window.alert('Seleziona almeno un apparato.'); return; }
      if (action.value === 'delete' && !window.confirm(`Rimuovere definitivamente ${count} apparati selezionati?`)) event.preventDefault();
    });
    fields();
    sites();
  }

  // Backup policy form: scope targets, vendor capabilities, schedule.
  const policy = document.querySelector('[data-backup-v2-form]');
  if (policy) {
    const scope = policy.querySelector('[data-scope-select]');
    const vendor = policy.querySelector('[data-vendor-select]');
    const customer = policy.querySelector('[data-customer-select]');
    const site = policy.querySelector('[data-site-select]');
    const device = policy.querySelector('[data-device-select]');
    const auto = policy.querySelector('[data-capability-auto]');
    const selectedCustomer = () => (customer && customer.value) || scope.dataset.customerContext || '';
    const filterOwned = (select) => {
      if (!select) return;
      const cid = selectedCustomer();
      let selectedValid = false;
      [...select.options].forEach((option, index) => {
        if (index === 0) return;
        const visible = !cid || option.dataset.customer === cid;
        option.hidden = !visible;
        option.disabled = !visible;
        if (option.selected && visible) selectedValid = true;
      });
      if (select.value && !selectedValid) select.value = '';
    };
    const inferCustomer = () => {
      if (!customer || customer.value) return;
      const source = scope.value === 'device' ? device : scope.value === 'site' ? site : null;
      const option = source && source.value ? source.selectedOptions[0] : null;
      if (option && option.dataset.customer) customer.value = option.dataset.customer;
    };
    const effectiveVendor = () => {
      if (scope.value === 'vendor') return (vendor && vendor.value) || '';
      if (scope.value === 'device') { const option = device && device.selectedOptions[0]; return (option && option.dataset.vendor) || ''; }
      return '';
    };
    const refreshCaps = () => {
      const v = effectiveVendor();
      policy.querySelectorAll('[data-capability]').forEach((e) => e.classList.toggle('hidden', !v || e.dataset.capability !== v));
      if (auto) auto.classList.toggle('hidden', !!v);
    };
    const refreshTargets = () => {
      const s = scope.value;
      policy.querySelectorAll('[data-targets]').forEach((e) => e.classList.toggle('hidden', !(e.dataset.targets || '').split(',').includes(s)));
      inferCustomer();
      filterOwned(site);
      filterOwned(device);
      refreshCaps();
    };
    const refreshSchedule = () => {
      const checked = policy.querySelector('[name=schedule_kind]:checked');
      const kind = (checked && checked.value) || 'daily';
      const time = policy.querySelector('[data-time-field]');
      if (time) time.classList.toggle('hidden', kind === 'six_hour');
      policy.querySelectorAll('[data-schedule-extra]').forEach((e) => e.classList.toggle('hidden', e.dataset.scheduleExtra !== kind));
    };
    scope.addEventListener('change', refreshTargets);
    if (vendor) vendor.addEventListener('change', refreshCaps);
    if (customer) customer.addEventListener('change', () => { filterOwned(site); filterOwned(device); refreshCaps(); });
    if (site) site.addEventListener('change', () => { inferCustomer(); refreshCaps(); });
    if (device) device.addEventListener('change', () => { inferCustomer(); filterOwned(device); refreshCaps(); });
    policy.querySelectorAll('[name=schedule_kind]').forEach((e) => e.addEventListener('change', refreshSchedule));
    refreshTargets();
    refreshSchedule();
  }
})();
