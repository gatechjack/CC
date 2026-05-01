// =============================================================================
// Trading Corp — single-VM infrastructure for the trading_corp bot.
//
// Deploys:
//   - Virtual Network + subnet (10.0.0.0/16)
//   - Network Security Group (in: 22 from home only, 80, 443)
//   - Static Standard SKU public IP
//   - Network Interface (links VM to subnet + public IP)
//   - Linux VM (Ubuntu 22.04 LTS, B2ms, SSH-key auth only)
//   - System-assigned Managed Identity on the VM
//   - Key Vault (RBAC mode)
//   - RBAC role assignments:
//       VM Managed Identity  → Key Vault Secrets User (read secrets at runtime)
//       Deploying user       → Key Vault Administrator (manage secrets via CLI)
//
// What this DOES NOT deploy (handled later in Phase 4-5):
//   - Anything inside the VM (Python, the repo, systemd units)
//   - Caddy / Let's Encrypt cert
//   - DNS A record (will add `trading.jacksumner.com → public IP` after VM is up)
//   - Backup vault (Phase 7)
//   - Defender for Cloud (one-time portal toggle)
//
// Deploy command:
//   az deployment group create `
//     --resource-group rg-shared-prod `
//     --template-file infra/main.bicep `
//     --parameters sshPublicKey="$(Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub -Raw)" `
//                  allowedSshIp="98.231.16.63" `
//                  deployingUserObjectId="$(az ad signed-in-user show --query id -o tsv)"
// =============================================================================


// =============================================================================
// PARAMETERS — values supplied at deploy time
// =============================================================================

@description('SSH public key for the VM admin user. Pasted from id_ed25519.pub.')
@secure()
param sshPublicKey string

@description('Single public IPv4 allowed to SSH to the VM (e.g. your Comcast IP). Will be combined with /32 mask.')
param allowedSshIp string

@description('Object ID of the user/principal running this deployment. They get Key Vault Administrator so they can manage secrets via CLI.')
param deployingUserObjectId string

@description('VM size. B2ms = 2 vCPU, 8GB RAM, ~$60/mo. Sufficient for trading_corp + future Playwright/Firefox.')
param vmSize string = 'Standard_B2ms'

@description('VM admin username (Linux). Convention is "azureuser".')
param adminUsername string = 'azureuser'

@description('Azure region. Defaults to the parent resource group location.')
param location string = resourceGroup().location

@description('Naming prefix used across all resources for clarity.')
param prefix string = 'tc-prod'


// =============================================================================
// VARIABLES — derived names so we don't repeat the prefix everywhere
// =============================================================================

var vnetName       = '${prefix}-vnet'
var subnetName     = '${prefix}-subnet'
var nsgName        = '${prefix}-nsg'
var publicIpName   = '${prefix}-pip'
var nicName        = '${prefix}-nic'
var vmName         = '${prefix}-vm'

// Key Vault names must be globally unique across all of Azure (3-24 chars,
// alphanumeric + hyphen). We use uniqueString() of the resource group ID
// to generate a deterministic suffix that won't collide with anyone else.
var keyVaultName   = 'kv-tc-${uniqueString(resourceGroup().id)}'

// Built-in role definition IDs — these are constants Microsoft publishes:
//   https://learn.microsoft.com/azure/role-based-access-control/built-in-roles
var roleKeyVaultSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var roleKeyVaultAdministrator = '00482a5a-887f-4fb3-b363-3b7fe8e74483'


// =============================================================================
// NETWORK SECURITY GROUP — firewall rules attached to the subnet
// =============================================================================

resource nsg 'Microsoft.Network/networkSecurityGroups@2023-09-01' = {
  name: nsgName
  location: location
  properties: {
    securityRules: [
      {
        // SSH (22) — restricted to your home IP only.
        // To update later, edit the source IP in this rule via the portal
        // or CLI: `az network nsg rule update ... --source-address-prefix NEW.IP.HERE/32`
        name: 'AllowSSHFromHome'
        properties: {
          priority: 1000
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '${allowedSshIp}/32'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '22'
        }
      }
      {
        // HTTP (80) — open to the internet. Required for:
        //   1. Let's Encrypt cert challenge (Caddy serves the .well-known path)
        //   2. Auto-redirect to HTTPS (handled by Caddy)
        name: 'AllowHTTP'
        properties: {
          priority: 1010
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'Internet'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '80'
        }
      }
      {
        // HTTPS (443) — open to the internet. This is where TradingView's
        // webhook traffic lands. Caddy terminates TLS and reverse-proxies
        // to localhost:8000 (trading_corp).
        name: 'AllowHTTPS'
        properties: {
          priority: 1020
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'Internet'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '443'
        }
      }
      // Default-deny rules for every other port are added implicitly by Azure
      // at priority 65500. We don't need to write them.
    ]
  }
}


// =============================================================================
// VIRTUAL NETWORK + SUBNET
// =============================================================================
// At single-VM scale this is overkill, but it's the right shape:
// - Lets us add more VMs (future family bots) into the same VNet later
// - The NSG attaches to the subnet so all VMs in this subnet share the rules
// - Future RDS/Postgres etc. can live in the same VNet with private endpoints

resource vnet 'Microsoft.Network/virtualNetworks@2023-09-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: ['10.0.0.0/16']    // 65k addresses, plenty of room
    }
    subnets: [
      {
        name: subnetName
        properties: {
          addressPrefix: '10.0.0.0/24'    // 256 addresses; one subnet for now
          networkSecurityGroup: {
            id: nsg.id
          }
        }
      }
    ]
  }
}


// =============================================================================
// PUBLIC IP ADDRESS — Standard SKU, static
// =============================================================================
// Standard SKU is required for production-grade VMs (zonal, secure-by-default).
// Static allocation means the IP doesn't change when the VM is restarted,
// so the DNS A record (trading.jacksumner.com) stays valid forever.

resource publicIp 'Microsoft.Network/publicIPAddresses@2023-09-01' = {
  name: publicIpName
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
  }
}


// =============================================================================
// NETWORK INTERFACE — virtual NIC linking VM to subnet + public IP
// =============================================================================

resource nic 'Microsoft.Network/networkInterfaces@2023-09-01' = {
  name: nicName
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          privateIPAllocationMethod: 'Dynamic'
          subnet: {
            id: '${vnet.id}/subnets/${subnetName}'
          }
          publicIPAddress: {
            id: publicIp.id
          }
        }
      }
    ]
  }
}


// =============================================================================
// VIRTUAL MACHINE — Ubuntu 22.04 LTS, SSH-only auth
// =============================================================================

resource vm 'Microsoft.Compute/virtualMachines@2023-09-01' = {
  name: vmName
  location: location
  identity: {
    // System-assigned Managed Identity gives the VM its own Entra ID identity.
    // We use this to grant the VM read access to Key Vault — the Python app
    // fetches secrets at runtime via Azure SDK, which auto-authenticates as
    // this identity. No secrets touch disk.
    type: 'SystemAssigned'
  }
  properties: {
    hardwareProfile: {
      vmSize: vmSize
    }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: '0001-com-ubuntu-server-jammy'
        sku: '22_04-lts-gen2'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        managedDisk: {
          storageAccountType: 'Standard_LRS'
        }
        diskSizeGB: 64
      }
    }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      linuxConfiguration: {
        // Disable password auth entirely — SSH key only. Best practice.
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: sshPublicKey
            }
          ]
        }
      }
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: nic.id
        }
      ]
    }
  }
}


// =============================================================================
// KEY VAULT — Azure's secrets store
// =============================================================================

resource keyVault 'Microsoft.KeyVault/vaults@2024-04-01-preview' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    // RBAC mode (newer best practice; replaces legacy "access policies")
    enableRbacAuthorization: true
    // Soft delete is now mandatory in Azure — recovers accidentally deleted
    // secrets within 7 days. Cannot be disabled.
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    // Allow public access for now (single VM connecting from outside VNet).
    // Future: lock down to VM subnet via private endpoint when scale demands.
    publicNetworkAccess: 'Enabled'
  }
}


// =============================================================================
// RBAC ROLE ASSIGNMENTS
// =============================================================================
// Two assignments:
// 1. The VM's managed identity gets "Key Vault Secrets User" — read-only.
//    The Python app at runtime reads (e.g.) the Telegram token via this.
// 2. The deploying user (you, jack@jacksumneryahoo.onmicrosoft.com) gets
//    "Key Vault Administrator" — full control. You'll use this to upload
//    secrets via `az keyvault secret set`.

resource vmKvSecretsRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  // GUID-based name, deterministic so re-running the deploy doesn't create
  // duplicate assignments. The triple (KV, VM identity, role) is unique per assignment.
  name: guid(keyVault.id, vm.id, roleKeyVaultSecretsUser)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      roleKeyVaultSecretsUser
    )
    principalId: vm.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource userKvAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, deployingUserObjectId, roleKeyVaultAdministrator)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      roleKeyVaultAdministrator
    )
    principalId: deployingUserObjectId
    principalType: 'User'
  }
}


// =============================================================================
// OUTPUTS — printed after deploy completes; used in subsequent commands
// =============================================================================

@description('Public IPv4 address of the VM. DNS A record will point at this.')
output publicIpAddress string = publicIp.properties.ipAddress

@description('Ready-to-paste SSH command for VM access.')
output sshCommand string = 'ssh ${adminUsername}@${publicIp.properties.ipAddress}'

@description('Key Vault name (globally unique). Used by upload-secrets script and the app at runtime.')
output keyVaultName string = keyVault.name

@description('Key Vault URI. Set as KEY_VAULT_URI env var on the VM for the Azure SDK to find it.')
output keyVaultUri string = keyVault.properties.vaultUri

@description('VM name. Useful for follow-up CLI commands.')
output vmName string = vm.name
