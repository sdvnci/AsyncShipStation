from collections.abc import Sequence
from typing import Literal

from ..common import (
    DeliveryConfirmationMethods,
    Endpoints,
    ErrorResponse,
    RateLimitError,
    ShipStationClient,
    ShipStationConnection,
    V1Address,
    V1Dimensions,
    V1Weight,
)
from ._types import (
    V1AdvancedOptions,
    V1BatchOrderCreationResponse,
    V1InsuranceOptions,
    V1InternationalOptions,
    V1Order,
    V1OrderItem,
    V1OrderLabel,
    V1OrderListResponse,
)


class OrderPortal(ShipStationClient):
    @classmethod
    async def where(
        cls: type["OrderPortal"],
        connection: ShipStationConnection,
        customerName: str | None = None,
        itemKeyword: str | None = None,
        createDateStart: str | None = None,
        createDateEnd: str | None = None,
        customsCountryCode: str | None = None,
        modifyDateStart: str | None = None,
        modifyDateEnd: str | None = None,
        orderDateStart: str | None = None,
        orderDateEnd: str | None = None,
        orderNumber: str | None = None,
        orderStatus: (
            Literal[
                "awaiting_payment",
                "awaiting_shipment",
                "pending_fulfillment",
                "shipped",
                "on_hold",
                "cancelled",
                "rejected_fulfillment",
            ]
            | None
        ) = None,
        paymentDateStart: str | None = None,
        paymentDateEnd: str | None = None,
        storeId: int | None = None,
        sortBy: Literal["OrderDate", "ModifyDate", "CreateDate"] | None = None,
        sortDir: Literal["DESC", "ASC"] | None = None,
        page: int | None = None,
        pageSize: int | None = None,
        identity: bool = False,
    ) -> tuple[int, ErrorResponse | V1OrderListResponse]:
        params = {
            "customerName": customerName,
            "itemKeyword": itemKeyword,
            "createDateStart": createDateStart,
            "createDateEnd": createDateEnd,
            "customsCountryCode": customsCountryCode,
            "modifyDateStart": modifyDateStart,
            "modifyDateEnd": modifyDateEnd,
            "orderDateStart": orderDateStart,
            "orderDateEnd": orderDateEnd,
            "orderNumber": orderNumber,
            "orderStatus": orderStatus,
            "paymentDateStart": paymentDateStart,
            "paymentDateEnd": paymentDateEnd,
            "storeId": storeId,
            "sortBy": sortBy,
            "sortDir": sortDir,
            "page": page,
            "pageSize": pageSize,
        }

        params = {k: v for k, v in params.items() if v is not None}

        endpoint = f"{connection.v1_endpoint}/{Endpoints.ORDERS.value}"

        try:
            res = await connection.request("GET", endpoint, "v1", params=params)  # type: ignore[arg-type]
            return cls.validate_response(
                res,
                (200, 201),
                V1OrderListResponse,
                identity=identity,
            )
        except RateLimitError:
            raise
        except Exception as e:
            return cls.parse_unknown_exception(e)

    @classmethod
    async def create_label(
        cls: type["OrderPortal"],
        connection: ShipStationConnection,
        orderId: int,
        carrierCode: str,
        serviceCode: str,
        confirmation: Literal[
            "none", "delivery", "signature", "adult_signature", "direct_signature"
        ],
        shipDate: str,
        weight: V1Weight | None = None,
        dimensions: V1Dimensions | None = None,
        insuranceoptions: V1InsuranceOptions | None = None,
        internationalOptions: V1InternationalOptions | None = None,
        advancedOptions: V1AdvancedOptions | None = None,
        testLabel: bool = False,
        identity: bool = False,
    ) -> tuple[int, V1OrderLabel | ErrorResponse]:
        payload = {
            "orderId": orderId,
            "carrierCode": carrierCode,
            "serviceCode": serviceCode,
            "confirmation": confirmation,
            "shipDate": shipDate,
            "weight": weight,
            "dimensions": dimensions,
            "insuranceOptions": insuranceoptions,
            "internationalOptions": internationalOptions,
            "advancedOptions": advancedOptions,
            "testLabel": testLabel,
        }

        payload = {k: v for k, v in payload.items() if v is not None}

        endpoint = (
            f"{connection.v1_endpoint}/{Endpoints.ORDERS.value}/createlabelfororder"
        )

        try:
            res = await connection.request("POST", endpoint, "v1", json=payload)  # type: ignore[arg-type]
            return cls.validate_response(
                res,
                (200, 201),
                V1OrderLabel,
                identity=identity,
            )
        except RateLimitError:
            raise
        except Exception as e:
            return cls.parse_unknown_exception(e)

    @classmethod
    async def update_order(
        cls: type["OrderPortal"],
        connection: ShipStationConnection,
        orderNumber: str,
        orderStatus: Literal[
            "awaiting_payment",
            "awaiting_shipment",
            "shipped",
            "on_hold",
            "cancelled",
            "pending_fulfillment",
        ],
        billTo: V1Address,
        shipTo: V1Address,
        orderId: int | None = None,
        orderKey: str | None = None,
        orderDate: str | None = None,  # in the form "2015-06-29T08:46:27.0000000"
        paymentDate: str | None = None,  # in the form "2015-06-29T08:46:27.0000000"
        shipByDate: (
            str | None
        ) = None,  # Usually provided by the marketplace, but can be provided by the user. In the form "2015-06-29T08:46:27.0000000"
        customerUsername: str | None = None,
        customerEmail: str | None = None,
        items: Sequence[V1OrderItem] | None = None,
        amountPaid: float | None = None,
        taxAmount: float | None = None,
        shippingAmount: float | None = None,
        customerNotes: str | None = None,
        internalNotes: str | None = None,
        gift: bool | None = None,
        giftMessage: str | None = None,
        paymentMethod: str | None = None,
        requestedShippingService: str | None = None,
        carrierCode: str | None = None,
        serviceCode: str | None = None,
        packageCode: str | None = None,
        confirmation: DeliveryConfirmationMethods | None = None,
        shipDate: str | None = None,  # in the form "2015-07-02"
        weight: V1Weight | None = None,
        dimensions: V1Dimensions | None = None,
        insuranceOptions: V1InsuranceOptions | None = None,
        internationalOptions: V1InternationalOptions | None = None,
        customsCountryCode: (
            str | None
        ) = None,  # default two-letter ISO Origin Country code for the Product.
        advancedOptions: V1AdvancedOptions | None = None,
        tagIds: Sequence[int] | None = None,
        identity: bool = False,
    ) -> tuple[int, V1Order | ErrorResponse]:
        """
        You can use this method to create a new order or update an existing order.
        If the orderKey is specified, ShipStation will attempt to locate the order with the
        specified orderKey. If found, the existing order with that key will be updated.
        If the orderKey is not found, a new order will be created with that orderKey.

        `orderNumber` and `orderKey` are usually the same, but they don't have to be.

        ## Do not include the `orderId` property when creating a new order.

        This call does not currently support partial updates. The entire resource must be provided in the body of the request.
        Only orders in an open status in ShipStation (awaiting_payment,awaiting_shipment, and on_hold) can be updated through this method.
        Orders in the `cancelled` and `shipped` states may not be updated.
        """

        payload = {
            "orderId": orderId,
            "orderNumber": orderNumber,
            "orderKey": orderKey,
            "orderStatus": orderStatus,
            "billTo": billTo,
            "shipTo": shipTo,
            "orderDate": orderDate,
            "paymentDate": paymentDate,
            "shipByDate": shipByDate,
            "customerUsername": customerUsername,
            "customerEmail": customerEmail,
            "items": items,
            "amountPaid": amountPaid,
            "taxAmount": taxAmount,
            "shippingAmount": shippingAmount,
            "customerNotes": customerNotes,
            "internalNotes": internalNotes,
            "gift": gift,
            "giftMessage": giftMessage,
            "paymentMethod": paymentMethod,
            "requestedShippingService": requestedShippingService,
            "carrierCode": carrierCode,
            "serviceCode": serviceCode,
            "packageCode": packageCode,
            "confirmation": confirmation,
            "shipDate": shipDate,
            "weight": weight,
            "dimensions": dimensions,
            "insuranceOptions": insuranceOptions,
            "internationalOptions": internationalOptions,
            "customsCountryCode": customsCountryCode,
            "advancedOptions": advancedOptions,
            "tagIds": tagIds,
        }

        payload = {k: v for k, v in payload.items() if v is not None}

        endpoint = f"{connection.v1_endpoint}/{Endpoints.ORDERS.value}/createorder"

        try:
            res = await connection.request("POST", endpoint, "v1", json=payload)  # type: ignore[arg-type]
            return cls.validate_response(
                res,
                (200, 201),
                V1Order,
                identity=identity,
            )
        except RateLimitError:
            raise
        except Exception as e:
            return cls.parse_unknown_exception(e)

    @classmethod
    async def create_order(
        cls: type["OrderPortal"],
        connection: ShipStationConnection,
        orderNumber: str,
        orderStatus: Literal[
            "awaiting_payment",
            "awaiting_shipment",
            "shipped",
            "on_hold",
            "cancelled",
            "pending_fulfillment",
        ],
        billTo: V1Address,
        shipTo: V1Address,
        orderKey: str | None = None,
        orderDate: str | None = None,  # in the form "2015-06-29T08:46:27.0000000"
        paymentDate: str | None = None,  # in the form "2015-06-29T08:46:27.0000000"
        shipByDate: (
            str | None
        ) = None,  # Usually provided by the marketplace, but can be provided by the user. In the form "2015-06-29T08:46:27.0000000"
        customerUsername: str | None = None,
        customerEmail: str | None = None,
        items: Sequence[V1OrderItem] | None = None,
        amountPaid: float | None = None,
        taxAmount: float | None = None,
        shippingAmount: float | None = None,
        customerNotes: str | None = None,
        internalNotes: str | None = None,
        gift: bool | None = None,
        giftMessage: str | None = None,
        paymentMethod: str | None = None,
        requestedShippingService: str | None = None,
        carrierCode: str | None = None,
        serviceCode: str | None = None,
        packageCode: str | None = None,
        confirmation: DeliveryConfirmationMethods | None = None,
        shipDate: str | None = None,  # in the form "2015-07-02"
        weight: V1Weight | None = None,
        dimensions: V1Dimensions | None = None,
        insuranceOptions: V1InsuranceOptions | None = None,
        internationalOptions: V1InternationalOptions | None = None,
        customsCountryCode: (
            str | None
        ) = None,  # default two-letter ISO Origin Country code for the Product.
        advancedOptions: V1AdvancedOptions | None = None,
        tagIds: Sequence[int] | None = None,
        identity: bool = False,
    ) -> tuple[int, V1Order | ErrorResponse]:
        """
        You can use this method to create/update a new order.
        If the orderKey is specified, ShipStation will attempt to locate the order with the
        specified orderKey. If found, the existing order with that key will be updated.
        If the orderKey is not found, a new order will be created with that orderKey.

        `orderNumber` and `orderKey` are usually the same, but they don't have to be.

        ## Do not include the `orderId` property when creating a new order

        This call does not currently support partial updates. The entire resource must be provided in the body of the request.
        Only orders in an open status in ShipStation (awaiting_payment,awaiting_shipment, and on_hold) can be updated through this method.
        Orders in the `cancelled` and `shipped` states may not be updated.
        """

        return await cls.update_order(
            connection,
            orderNumber=orderNumber,
            orderStatus=orderStatus,
            billTo=billTo,
            shipTo=shipTo,
            orderKey=orderKey,
            orderDate=orderDate,
            paymentDate=paymentDate,
            shipByDate=shipByDate,
            customerUsername=customerUsername,
            customerEmail=customerEmail,
            items=items,
            amountPaid=amountPaid,
            taxAmount=taxAmount,
            shippingAmount=shippingAmount,
            customerNotes=customerNotes,
            internalNotes=internalNotes,
            gift=gift,
            giftMessage=giftMessage,
            paymentMethod=paymentMethod,
            requestedShippingService=requestedShippingService,
            carrierCode=carrierCode,
            serviceCode=serviceCode,
            packageCode=packageCode,
            confirmation=confirmation,
            shipDate=shipDate,
            weight=weight,
            dimensions=dimensions,
            insuranceOptions=insuranceOptions,
            internationalOptions=internationalOptions,
            customsCountryCode=customsCountryCode,
            advancedOptions=advancedOptions,
            tagIds=tagIds,
        )

    @classmethod
    async def create_or_update_orders(
        cls: type["OrderPortal"],
        connection: ShipStationConnection,
        orders: Sequence[V1Order],
        identity: bool = False,
    ) -> tuple[int, V1BatchOrderCreationResponse | ErrorResponse]:
        """
        This endpoint can be used to create or update multiple orders in one request. If the orderKey is specified, ShipStation will attempt to locate the order with the specified orderKey. If found, the existing order with that key will be updated. If the orderKey is not found, a new order will be created with that orderKey.

        For split orders, the orderKey is always required when creating or updating orders, and the orderId is always required for updates.

        This call does not currently support partial updates; the entire resource must be provided in the body of the request.
        """

        endpoint = f"{connection.v1_endpoint}/{Endpoints.ORDERS.value}/createorders"

        try:
            res = await connection.request("POST", endpoint, "v1", json=orders)  # type: ignore[arg-type]
            return cls.validate_response(
                res,
                (200, 201),
                V1BatchOrderCreationResponse,
                identity=identity,
            )
        except RateLimitError:
            raise
        except Exception as e:
            return cls.parse_unknown_exception(e)
