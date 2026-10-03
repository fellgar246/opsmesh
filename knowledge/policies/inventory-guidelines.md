id: inventory-guidelines
version: 2026.1
effective_start: 2026-01-01
scope: global

## order-quantity

A proposed quantity below the supplier minimum order quantity is invalid.
A proposed quantity must be a multiple of the supplier case pack. A missing case pack is treated as 1.
Available stock that already covers selected demand does not require a purchase.
