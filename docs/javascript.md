# Aries Framework JavaScript Interoperability

## Runsets with AFJ

| Runset | ACME<br>(Issuer) | Bob<br>(Holder) | Faber<br>(Verfier) | Mallory<br>(Holder) | Scope | Results | 
| ------ | :--------------: | :-------------: | :----------------: | :-----------------: | ----- | :-----: | 
| [acapy-afj](#runset-acapy-afj) | acapy-main<br>0.7.0-rc1 | javascript<br>1.0.0 | acapy-main<br>0.7.0-rc1 | acapy-main<br>0.7.0-rc1 | AIP 1.0 | [**12 / 12<br>100%**](https://allure.vonx.io/api/allure-docker-service/projects/acapy-b-javascript/reports/latest/index.html?redirect=false#behaviors) |
| [acapy-dotnet-javascript](#runset-acapy-dotnet-javascript) | acapy-main<br>0.7.0-rc1 | javascript<br>1.0.0 | dotnet-master<br> | acapy-main<br>0.7.0-rc1 | AIP 1.0 | [**10 / 12<br>83%**](https://allure.vonx.io/api/allure-docker-service/projects/acapy-b-javascript-f-dotnet/reports/latest/index.html?redirect=false#behaviors) |
| [afj-acapy](#runset-afj-acapy) | javascript<br>1.0.0 | acapy-main<br>0.7.0-rc1 | javascript<br>1.0.0 | javascript<br>1.0.0 | AIP 1.0 | [**7 / 12<br>58%**](https://allure.vonx.io/api/allure-docker-service/projects/javascript-b-acapy/reports/latest/index.html?redirect=false#behaviors) |
| [afj-dotnet](#runset-afj-dotnet) | javascript<br>1.0.0 | dotnet-master<br> | javascript<br>1.0.0 | javascript<br>1.0.0 | AIP 1.0 | [**12 / 12<br>100%**](https://allure.vonx.io/api/allure-docker-service/projects/javascript-b-dotnet/reports/latest/index.html?redirect=false#behaviors) |
| [afj](#runset-afj) | javascript<br>1.0.0 | javascript<br>1.0.0 | javascript<br>1.0.0 | javascript<br>1.0.0 | AIP 1.0 | [**17 / 17<br>100%**](https://allure.vonx.io/api/allure-docker-service/projects/javascript/reports/latest/index.html?redirect=false#behaviors) |
| [dotnet-javascript](#runset-dotnet-javascript) | dotnet-master<br> | javascript<br>1.0.0 | dotnet-master<br> | dotnet-master<br> | AIP 1.0 | [**5 / 12<br>41%**](https://allure.vonx.io/api/allure-docker-service/projects/dotnet-b-javascript/reports/latest/index.html?redirect=false#behaviors) |

## Runset Notes

### Runset **acapy-afj**

Runset Name: ACA-PY to AFJ

```tip
**Latest results: 12 out of 12 (100%)**


*Last run: Sun Jul 11 01:29:22 UTC 2021*
```

#### Current Runset Status

Most of the tests are running. The tests not passing are being investigated.

*Status Note Updated: 2021.03.18*

#### Runset Details

- Results grouped by [executed Aries RFCs executed](https://allure.vonx.io/api/allure-docker-service/projects/acapy-b-javascript/reports/latest/index.html?redirect=false#behaviors)
- Results by [history](https://allure.vonx.io/allure-docker-service-ui/projects/acapy-b-javascript/reports/latest)


### Runset **acapy-dotnet-javascript**

Runset Name: ACA-PY to AF-.NET to AFJ

```tip
**Latest results: 10 out of 12 (83%)**


*Last run: Sun Jul 11 01:42:45 UTC 2021*
```

#### Current Runset Status

All tests are working, except for three tests that include Faber in the test run as an issuer.
These tests are; T001-RFC0037@1.2, T001.2-RFC0037@1.2, T001.4-RFC0037@1.1 . Further investigation 
is required to determine the issue in these three tests.

*Status Note Updated: 2021.03.18*

#### Runset Details

- Results grouped by [executed Aries RFCs executed](https://allure.vonx.io/api/allure-docker-service/projects/acapy-b-javascript-f-dotnet/reports/latest/index.html?redirect=false#behaviors)
- Results by [history](https://allure.vonx.io/allure-docker-service-ui/projects/acapy-b-javascript-f-dotnet/reports/latest)


### Runset **afj-acapy**

Runset Name: AFJ to ACA-PY

```tip
**Latest results: 7 out of 12 (58%)**


*Last run: Sun Jul 11 01:57:46 UTC 2021*
```

#### Current Runset Status

All AIP10 tests are currently running, except for the tests with the holder proposing a proof, 
as that feature is not supported in AFJ.

*Status Note Updated: 2021.03.17*

#### Runset Details

- Results grouped by [executed Aries RFCs executed](https://allure.vonx.io/api/allure-docker-service/projects/javascript-b-acapy/reports/latest/index.html?redirect=false#behaviors)
- Results by [history](https://allure.vonx.io/allure-docker-service-ui/projects/javascript-b-acapy/reports/latest)


### Runset **afj-dotnet**

Runset Name: AFJ to AF-.NET

```tip
**Latest results: 12 out of 12 (100%)**


*Last run: Sun Jul 11 02:03:27 UTC 2021*
```

#### Current Runset Status

All of the tests being executed in this runset are passing.

*Status Note Updated: 2021.03.05*

#### Runset Details

- Results grouped by [executed Aries RFCs executed](https://allure.vonx.io/api/allure-docker-service/projects/javascript-b-dotnet/reports/latest/index.html?redirect=false#behaviors)
- Results by [history](https://allure.vonx.io/allure-docker-service-ui/projects/javascript-b-dotnet/reports/latest)


### Runset **afj**

Runset Name: AFJ to AFJ

```tip
**Latest results: 17 out of 17 (100%)**


*Last run: Sun Jul 11 02:13:07 UTC 2021*
```

#### Current Runset Status

All of the tests being executed in this runset are passing.

*Status Note Updated: 2021.03.05*

#### Runset Details

- Results grouped by [executed Aries RFCs executed](https://allure.vonx.io/api/allure-docker-service/projects/javascript/reports/latest/index.html?redirect=false#behaviors)
- Results by [history](https://allure.vonx.io/allure-docker-service-ui/projects/javascript/reports/latest)


### Runset **dotnet-javascript**

Runset Name: AF-.NET to AFJ

```tip
**Latest results: 5 out of 12 (41%)**


*Last run: Sun Jul 11 02:18:22 UTC 2021*
```

#### Current Runset Status

More tests are failing than are passing when Aries Framework .NET is playing the issuer role. More investigation is needed.

*Status Note Updated: 2021.03.18*

#### Runset Details

- Results grouped by [executed Aries RFCs executed](https://allure.vonx.io/api/allure-docker-service/projects/dotnet-b-javascript/reports/latest/index.html?redirect=false#behaviors)
- Results by [history](https://allure.vonx.io/allure-docker-service-ui/projects/dotnet-b-javascript/reports/latest)

Jump back to the [interoperability summary](./README.md).

