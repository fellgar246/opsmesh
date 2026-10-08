# Services

This directory is an intentional package. It is reserved for domain services.

Inventory, forecast, supplier, finance, and procurement for the local simulator are implemented in the API and exposed over local HTTP.

Workflows read forecasts through a provider port. The default provider uses the local fixture. An external provider is optional and off unless it is selected.
