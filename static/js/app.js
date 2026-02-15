// SOAP-to-REST Proxy - Frontend JavaScript

// Load services on page load
document.addEventListener('DOMContentLoaded', () => {
    loadServices();
    loadGatewayConfig();

    // Setup gateway config form handler
    document.getElementById('gateway-config-form').addEventListener('submit', saveGatewayConfig);
});

// Handle WSDL upload form
document.getElementById('upload-form').addEventListener('submit', async (e) => {
    e.preventDefault();

    const submitBtn = document.getElementById('submit-btn');
    const btnText = submitBtn.querySelector('.btn-text');
    const btnLoader = submitBtn.querySelector('.btn-loader');
    const resultBox = document.getElementById('upload-result');

    // Disable submit button
    submitBtn.disabled = true;
    btnText.style.display = 'none';
    btnLoader.style.display = 'inline';

    // Get form data
    const formData = new FormData();
    const wsdlFile = document.getElementById('wsdl-file').files[0];
    const wsdlUrl = document.getElementById('wsdl-url').value;
    const serviceName = document.getElementById('service-name').value;
    const autoRegister = document.getElementById('auto-register')?.checked || false;

    if (wsdlFile) {
        formData.append('wsdl_file', wsdlFile);
    } else if (wsdlUrl) {
        formData.append('wsdl_url', wsdlUrl);
    } else {
        showResult(resultBox, 'error', 'Please provide either a WSDL file or URL');
        resetSubmitButton(submitBtn, btnText, btnLoader);
        return;
    }

    if (serviceName) {
        formData.append('service_name', serviceName);
    }

    formData.append('auto_register_gateway', autoRegister);

    try {
        const response = await fetch('/api/convert', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (response.ok) {
            let message = `<h3>✅ WSDL Converted Successfully!</h3>`;
            message += `<p><strong>Service:</strong> ${data.service_name}</p>`;
            message += `<p><strong>Operations:</strong> ${data.operations_count} SOAP operations converted to REST endpoints</p>`;

            if (data.gateway_registered) {
                message += `<p><strong>Gateway Registration:</strong> ✅ Also registered with MCP Gateway</p>`;
                message += `<p><strong>MCP Endpoint:</strong></p>`;
                message += `<code>${data.mcp_endpoint}</code>`;
            } else if (data.gateway_error) {
                message += `<p><strong>Gateway Registration:</strong> ⚠️ Failed - ${data.gateway_error}</p>`;
            } else {
                message += `<p><strong>Note:</strong> Service converted but not registered with Gateway (checkbox was unchecked)</p>`;
            }

            showResult(resultBox, 'success', message);

            // Reset form
            document.getElementById('upload-form').reset();

            // Reload services list
            setTimeout(() => loadServices(), 500);

        } else {
            showResult(resultBox, 'error', `<h3>❌ Error</h3><p>${data.error}</p>`);
        }

    } catch (error) {
        showResult(resultBox, 'error', `<h3>❌ Error</h3><p>${error.message}</p>`);
    } finally {
        resetSubmitButton(submitBtn, btnText, btnLoader);
    }
});

// Show result message
function showResult(element, type, message) {
    element.className = `result-box ${type}`;
    element.innerHTML = message;
    element.style.display = 'block';

    // Auto-hide after 10 seconds
    setTimeout(() => {
        element.style.display = 'none';
    }, 10000);
}

// Reset submit button
function resetSubmitButton(btn, textEl, loaderEl) {
    btn.disabled = false;
    textEl.style.display = 'inline';
    loaderEl.style.display = 'none';
}

// Load services list
async function loadServices() {
    const servicesList = document.getElementById('services-list');
    servicesList.innerHTML = '<p class="loading">Loading services...</p>';

    try {
        const response = await fetch('/api/services');
        const data = await response.json();

        if (data.services.length === 0) {
            servicesList.innerHTML = '<p class="loading">No services converted yet. Upload a WSDL file to get started!</p>';
            return;
        }

        servicesList.innerHTML = '';

        data.services.forEach(service => {
            servicesList.appendChild(createServiceCard(service));
        });

    } catch (error) {
        servicesList.innerHTML = `<p class="loading" style="color: red;">Error loading services: ${error.message}</p>`;
    }
}

// Create service card element
function createServiceCard(service) {
    const card = document.createElement('div');
    card.className = 'service-card';
    card.id = `service-${service.id}`;

    let html = `
        <h3>🧼 ${service.name}</h3>
        <div class="service-meta">
            <p>${service.description || 'No description'}</p>
            <p>Operations: ${service.operations_count} | Created: ${new Date(service.created_at).toLocaleDateString()}</p>
        </div>
    `;

    // Gateway status
    html += '<div class="gateway-status">';
    if (service.gateway_registered) {
        html += `
            <span class="badge success">✅ Registered with Gateway</span>
            <div class="endpoint-code">${service.gateway_mcp_endpoint}</div>
            <button class="btn btn-danger btn-small" onclick="unregisterGateway('${service.id}')">
                Unregister from Gateway
            </button>
        `;
    } else {
        html += `
            <span class="badge warning">⚠️ Not registered with Gateway</span>
            <button class="btn btn-success btn-small" onclick="registerGateway('${service.id}')">
                Register with Gateway
            </button>
        `;
    }
    html += '</div>';

        // Security status
    const secConfig = service.security_config;
    const authType = secConfig ? secConfig.auth_type : 'none';
    const authLabels = {
        'none': '🔓 No Auth',
        'wsse_username': '🔐 WS-Security',
        'basic_auth': '🔑 Basic Auth',
        'client_cert': '📜 Client Cert'
    };
    html += `<div class="security-status">
        <span class="badge ${authType !== 'none' ? 'success' : ''}">${authLabels[authType] || '🔓 No Auth'}</span>
        <button class="btn btn-secondary btn-small" onclick="showSecurityDialog('${service.id}', '${service.name}')">
            🔒 Configure Security
        </button>
    </div>`;

    // Actions
    html += `
        <div class="actions">
            <button class="btn btn-secondary" onclick="downloadOpenAPI('${service.id}', 'yaml')">
                📥 Download OpenAPI (YAML)
            </button>
            <button class="btn btn-secondary" onclick="downloadOpenAPI('${service.id}', 'json')">
                📥 Download OpenAPI (JSON)
            </button>
            <button class="btn btn-danger" onclick="deleteService('${service.id}', '${service.name}')">
                🗑️ Delete Service
            </button>
        </div>
    `;

    card.innerHTML = html;
    return card;
}

// Download OpenAPI spec
function downloadOpenAPI(serviceId, format) {
    window.location.href = `/api/services/${serviceId}/openapi.${format}`;
}

// Register service with Gateway
async function registerGateway(serviceId) {
    if (!confirm('Register this service with MCP Gateway?')) {
        return;
    }

    try {
        const response = await fetch(`/api/services/${serviceId}/register-gateway`, {
            method: 'POST'
        });

        const data = await response.json();

        if (response.ok) {
            alert(`✅ Registered with Gateway!\n\nMCP Endpoint:\n${data.mcp_endpoint}`);
            loadServices();
        } else {
            alert(`❌ Error: ${data.error}`);
        }

    } catch (error) {
        alert(`❌ Error: ${error.message}`);
    }
}

// Unregister service from Gateway
async function unregisterGateway(serviceId) {
    if (!confirm('Unregister this service from MCP Gateway?')) {
        return;
    }

    try {
        const response = await fetch(`/api/services/${serviceId}/unregister-gateway`, {
            method: 'DELETE'
        });

        const data = await response.json();

        if (response.ok) {
            alert('✅ Unregistered from Gateway successfully!');
            loadServices();
        } else {
            alert(`❌ Error: ${data.error}`);
        }

    } catch (error) {
        alert(`❌ Error: ${error.message}`);
    }
}

// Delete service
async function deleteService(serviceId, serviceName) {
    if (!confirm(`Delete service "${serviceName}"?\n\nThis will also unregister it from Gateway if registered.`)) {
        return;
    }

    try {
        const response = await fetch(`/api/services/${serviceId}`, {
            method: 'DELETE'
        });

        const data = await response.json();

        if (response.ok) {
            alert('✅ Service deleted successfully!');
            loadServices();
        } else {
            alert(`❌ Error: ${data.error}`);
        }

    } catch (error) {
        alert(`❌ Error: ${error.message}`);
    }
}

// Load gateway configuration
async function loadGatewayConfig() {
    try {
        const response = await fetch('/api/gateway/config');
        const data = await response.json();

        if (data.gateway_url) {
            document.getElementById('gateway-url').value = data.gateway_url;
        }
        if (data.gateway_token) {
            document.getElementById('gateway-token').value = data.gateway_token;
        }

        // Update status
        const statusEl = document.getElementById('gateway-status');
        if (data.configured) {
            statusEl.textContent = 'Connected ✅';
            statusEl.className = 'badge success';
        } else {
            statusEl.textContent = 'Not Configured ⚠️';
            statusEl.className = 'badge warning';
        }
    } catch (error) {
        console.error('Error loading gateway config:', error);
    }
}

// Save gateway configuration
async function saveGatewayConfig(e) {
    e.preventDefault();

    const saveBtn = document.getElementById('save-gateway-btn');
    const btnText = saveBtn.querySelector('.btn-text');
    const btnLoader = saveBtn.querySelector('.btn-loader');
    const resultBox = document.getElementById('gateway-config-result');

    // Disable button
    saveBtn.disabled = true;
    btnText.style.display = 'none';
    btnLoader.style.display = 'inline';

    const gatewayUrl = document.getElementById('gateway-url').value;
    const gatewayToken = document.getElementById('gateway-token').value;

    try {
        const response = await fetch('/api/gateway/config', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                gateway_url: gatewayUrl,
                gateway_token: gatewayToken
            })
        });

        const data = await response.json();

        if (response.ok) {
            resultBox.className = 'info-box success';
            resultBox.innerHTML = '<p><strong>✅ Configuration saved successfully!</strong></p><p>You can now enable auto-registration when uploading WSDL files.</p>';
            resultBox.style.display = 'block';

            // Update status
            document.getElementById('gateway-status').textContent = 'Connected ✅';

            // Reload page to update gateway checkboxes
            setTimeout(() => window.location.reload(), 1500);
        } else {
            resultBox.className = 'info-box error';
            resultBox.innerHTML = `<p><strong>❌ Error:</strong> ${data.error}</p>`;
            resultBox.style.display = 'block';
        }

    } catch (error) {
        resultBox.className = 'info-box error';
        resultBox.innerHTML = `<p><strong>❌ Error:</strong> ${error.message}</p>`;
        resultBox.style.display = 'block';
    } finally {
        saveBtn.disabled = false;
        btnText.style.display = 'inline';
        btnLoader.style.display = 'none';

        // Hide result after 5 seconds
        setTimeout(() => {
            resultBox.style.display = 'none';
        }, 5000);
    }
}

// Toggle security fields based on auth type selection
function toggleSecurityFields() {
    const authType = document.getElementById('auth-type').value;
    document.getElementById('wsse-fields').style.display = authType === 'wsse_username' ? 'block' : 'none';
    document.getElementById('basic-auth-fields').style.display = authType === 'basic_auth' ? 'block' : 'none';
    document.getElementById('client-cert-fields').style.display = authType === 'client_cert' ? 'block' : 'none';
}

// Build security config object from form fields
function buildSecurityConfig() {
    const authType = document.getElementById('auth-type').value;
    const config = { auth_type: authType };

    if (authType === 'wsse_username') {
        config.wsse = {
            username: document.getElementById('wsse-username').value,
            password: document.getElementById('wsse-password').value,
            use_digest: document.getElementById('wsse-digest').checked,
            add_timestamp: document.getElementById('wsse-timestamp').checked
        };
    } else if (authType === 'basic_auth') {
        config.basic_auth = {
            username: document.getElementById('ba-username').value,
            password: document.getElementById('ba-password').value
        };
    } else if (authType === 'client_cert') {
        config.client_cert = {
            cert_path: document.getElementById('cert-path').value,
            key_path: document.getElementById('key-path').value,
            ca_bundle_path: document.getElementById('ca-path').value
        };
    }

    return config;
}

// Show security config dialog for an existing service
async function showSecurityDialog(serviceId, serviceName) {
    const authType = prompt(
        `Configure security for "${serviceName}"\n\n` +
        `Enter auth type:\n` +
        `  none - No authentication\n` +
        `  wsse_username - WS-Security UsernameToken\n` +
        `  basic_auth - HTTP Basic Auth\n` +
        `  client_cert - Client Certificate\n\n` +
        `Current setting will be replaced.`,
        'none'
    );

    if (!authType) return;

    const validTypes = ['none', 'wsse_username', 'basic_auth', 'client_cert'];
    if (!validTypes.includes(authType)) {
        alert('Invalid auth type. Must be one of: ' + validTypes.join(', '));
        return;
    }

    const config = { auth_type: authType };

    if (authType === 'wsse_username') {
        config.wsse = {
            username: prompt('WSSE Username:') || '',
            password: prompt('WSSE Password:') || '',
            use_digest: confirm('Use password digest?'),
            add_timestamp: confirm('Add timestamp token?')
        };
    } else if (authType === 'basic_auth') {
        config.basic_auth = {
            username: prompt('Basic Auth Username:') || '',
            password: prompt('Basic Auth Password:') || ''
        };
    } else if (authType === 'client_cert') {
        config.client_cert = {
            cert_path: prompt('Client certificate path:') || '',
            key_path: prompt('Private key path:') || '',
            ca_bundle_path: prompt('CA bundle path (optional):') || ''
        };
    }

    try {
        const response = await fetch(`/api/services/${serviceId}/security`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });

        const data = await response.json();

        if (response.ok) {
            alert(`Security config saved for ${serviceName}`);
            loadServices();
        } else {
            alert(`Error: ${data.error}`);
        }
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

// Logout function
function logout() {
    if (confirm('Are you sure you want to logout?')) {
        window.location.href = '/logout';
    }
}
